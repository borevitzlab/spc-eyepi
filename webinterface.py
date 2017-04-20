#!/usr/bin/python3

import fnmatch
import json
import socket
import subprocess
import dbm
import logging
import re
from jinja2 import Environment, FileSystemLoader
from configparser import ConfigParser
from datetime import datetime
from functools import wraps
from glob import glob

from werkzeug.wsgi import DispatcherMiddleware
from werkzeug.serving import run_simple
from flask import Flask, redirect, url_for, send_file, abort, Response, render_template, jsonify, send_from_directory, \
    request
from flask_bcrypt import Bcrypt

from libs.Camera import *
from flask import g

import browsepy

try:
    # generate a new machine id if one does not already exist
    if not os.path.exists("/etc/machine-id"):
        os.system("systemd-machine-id-setup")
    os.system("chown -R tor:tor /home/tor_private ")
    os.system("chown -R tor:tor /var/lib/tor ")
except:
    print("something went wrong, oh well...")

browsepy.app.config.update(
    APPLICATION_ROOT="/filesystem",
    directory_base="/home/images",
    directory_start="/home/images",
    directory_remove="/home/images",
)

app = Flask(__name__, static_url_path='/static')
app.debug = True
bcrypt = Bcrypt(app)

if socket.gethostname() != "VorvadossTwo":
    kmsghandler = logging.FileHandler("/dev/kmsg", 'w')
    app.logger.addHandler(kmsghandler)


def setup_ap():
    """
    Starts the wireless adapter in access point mode using create_ap

    """
    env = Environment(loader=FileSystemLoader('templates'))
    template = env.get_template("createap")
    interface = os.popen("ip link | cut -c4- | grep ^w | sed 's/:.*//'").read().rstrip()
    # Enumerates ip link, removes first 4 characters, filters lines that start with w then removes text following ':'
    netprofile = open('/usr/lib/systemd/system/create_ap.service', 'w')
    netprofile.write(template.render(interface=interface))
    netprofile.close()
    print("Starting AP")
    os.system("systemctl start create_ap.service")


def sanitizeconfig(towriteconfig, filename: str):
    """
    This method is meant to be a sanitiser for the configuration file, before it gets written.

    :param configparser.ConfigParser towriteconfig: config object to write to disk.
    :param str filename: filename to write to.
    """
    with open(filename, 'w') as configfile:
        towriteconfig.write(configfile)


def get_time() -> str:
    """
    Almost iso8601 formatted time string.

    :return: time string formatted with 'YYYY-MM-DD HH:mm:ss'
    :rtype: str
    """
    return str(datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))


def get_hostname() -> str:
    """
    Hostname of the system as a string.

    :return: the current hostname
    :rtype: str
    """
    return str(socket.gethostname())


def get_version() -> str:
    """
    Current git version of spc-eyepi.

    :return: version
    :rtype: str
    """
    return subprocess.check_output(["/usr/bin/git describe --always"], shell=True).decode()


try:
    app.jinja_env.globals.update(get_time=get_time)
    app.jinja_env.globals.update(get_hostname=get_hostname)
    app.jinja_env.globals.update(version=get_version())
except:
    pass


def check_auth(username: str, password: str) -> bool:
    """
    validataion of auth.
    Username and password are checked against the bcrypt password hash in the database.

    :param str username:
    :param str password:
    :return: whether the supplied password matches the hash of the one stored in the database
    :rtype: bool
    """
    ubytes = bytes(username, 'utf-8')

    with dbm.open('db', 'r') as db:
        if ubytes in db.keys() and bcrypt.check_password_hash(db[ubytes].decode('utf-8'), password):
            return True
    return False


def requires_auth(f):
    """
    Decorator for wrapping a view and requiring auth.

    :param types.FunctionType f: view function
    :return decorated: wrapped
    :rtype: types.FunctionType
    """

    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)

    return decorated


@browsepy.app.before_request
@requires_auth
def require_login():
    """
    Hackery to make browsepy work with login.
    """
    return


def authenticate():
    """
    really this should just return a 404 for it to be really secure.
    But I use the message sometimes.

    :return: 401 Access Denied Response
    :rtype: Response
    """
    return Response('Access DENIED!', 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})


@app.errorhandler(404)
def not_found(error):
    """
    404 error handler

    :param error:
    :return: 404 page
    :rtype: Response
    """
    return render_template('page_not_found.html'), 404


@app.errorhandler(500)
def server_error(error):
    """
    500 error handler

    :param error:
    :return: 500 page
    :rtype: Response
    """
    return render_template('server_error.html'), 500


@app.errorhandler(401)
def bad_auth(error):
    """
    401 error handler

    :param error:
    :return: 401 page
    :rtype: Response
    """
    return render_template('bad_auth.html'), 401


def add_user(username: str, password_to_set: str, adminpass: str = None) -> bool:
    """
    Creates a new user in the small db, or changes the password if it exists.

    If the admin password is provided and matches the 'admin' users password hash in the db, allows adding of new users
    and modification of other users accounts.

    Hashes passwords using :mod:`flask_bcrypt` before storing them in the db.

    TODO: this should be moved to :mod:`api`

    :param str username: username to modify
    :param str password_to_set: plaintext password
    :param str adminpass:
    :return: whether the operation was sucessful
    :rtype: bool
    """

    password_hash = bcrypt.generate_password_hash(password_to_set)
    db = dbm.open('db', 'c')
    # later only allow users control over their own password and admin to add later.
    # allow global admin password to change everything.
    if b'admin' in db.keys() and bcrypt.check_password_hash(db[b'admin'], adminpass):
        db[username] = password_hash
        db.close()
        return True

    # for each username, only allow the correct hash to change the password
    for username_, hash_ in db.items():
        if username_ in db.keys() and bcrypt.check_password_hash(hash_, adminpass):
            db[username] = password_hash
            db.close()
            return True
    db.close()
    return False


@app.route("/imgs/<path:path>")
def get_image(path):
    """
    View to serve an image (like from a camera), from the static/temp directory.

    the static/temp dir is normally symlinked to /dev/shm or /tmp, the location where the main capture script drops the
    last image.

    :param str path: path/name of the image, without the extension (.jpg is added)
    :return: image response
    :rtype: Response
    """

    if '..' in path or path.startswith('/'):
        abort(404)
    return send_file(os.path.join("static", "temp", path + ".jpg"))


def cap_lock_wait(port: str, serialnumber: str) -> bool:
    """
    captures and writes an image to static/temp with the file name of the serial number.

    todo: Does this even work? this is old and prbably broken FIX MEEEEE!

    :param str port: sub port
    :param str serialnumber: serial number of the camera
    :return: whether a frame has been written to disk
    :rtype: bool
    """
    try:
        a = subprocess.check_output(
            "gphoto2 --port=" + str(port) + " --capture-preview --force-overwrite --filename='static/temp/" + str(
                serialnumber) + ".jpg'", shell=True).decode()
        print(a)
        return False
    except subprocess.CalledProcessError as e:
        print(e.output)
        return True


def capture_preview(serialnumber: str) -> bool:
    """
    capture a preview image once.

    todo: see :func:`cap_lock_wait`

    :param serialnumber:
    :return: whether the capture was a success
    :rtype: bool
    """
    try:
        a = subprocess.check_output("gphoto2 --auto-detect", shell=True).decode()
        for port in re.finditer("usb:", a):
            port = a[port.start():port.end() + 7]
            cmdret = subprocess.check_output('gphoto2 --port "' + port + '" --get-config serialnumber',
                                             shell=True).decode()
            _serialnumber = cmdret[cmdret.find("Current: ") + 9: len(cmdret) - 1]
            if _serialnumber == serialnumber:
                tries = 0
                while tries < 10 and cap_lock_wait(port, serialnumber):
                    tries += 1
                    time.sleep(1)
                return True

    except subprocess.CalledProcessError as e:
        print(str(e))
    return False


@app.route("/preview_cam", methods=["GET"])
def preview():
    """
    This gets a preview image from a camera, based on the url parameter "serialnumber"

    so the endpoint wouild be /preview_cam?serialnumber=dfjkaghsdfysadftiqw

    todo: see :func:`cap_lock_wait`

    :return: the image file or the string "fail"
    :rtype: Response or str
    """
    if request.method == 'GET':
        if request.args.get("serialnumber"):
            serialnumber = request.args.get("serialnumber")
            preview = capture_preview(serialnumber)
            return send_file("static/temp/" + str(serialnumber) + ".jpg")
        else:
            return "fail"
    else:
        return "fail"


@app.route("/available_networks", methods=["GET"])
def available_networks():
    """
    Gets the available networks doing a scan with ... wlp6s0?

    todo: get fluffybunny to fix this.

    :return: Streamed response of networks as they are enumerated by the scan, newline separated.
    :rtype: Response
    """

    def generate_networks():
        """
        generator function for streaming scan of wifi networks.

        todo: get fluffybunny to fix this.
        """
        networks = str.splitlines(os.popen('iw dev wlan0 scan | grep "SSID: " | cut -c 8- | sort |uniq').read())
        networks = [x for x in networks if "x00" not in x]
        for net in networks:
            yield net + '\n'

    return Response(generate_networks(), mimetype='text/plain')


@app.route("/wifi", methods=["GET"])
def wifi():
    """
    wifi configuration view.
    """
    return render_template("wifi.html")


@app.route("/focus_cams")
def focus():
    """
    view function to focus the cameras.

    todo: move to :mod:`api`
    """

    a = subprocess.check_output("gphoto2 --auto-detect", shell=True).decode()

    for port in re.finditer("usb:", a):
        port = a[port.start():port.end() + 7]
        cmdret = subprocess.check_output('gphoto2 --port "' + port + '" --get-config serialnumber',
                                         shell=True).decode()
    return "success"


@app.route("/sync_hwclock")
@requires_auth
def sync_hwclock():
    """
    synchronises the hardware clock with the system clock, and redirects to 'config' endpoint.

    todo: move to :mod:`api`
    """
    print("Synchronising hwclock")
    try:
        cmd = subprocess.check_output("hwclock --systohc", shell=True)
    except Exception as e:
        print("There was a problem Synchronising the hwclock. Debug me please.")
        print("Exception: " + str(e))
        return render_template('server_error.html'), 500

    return redirect(url_for('config'))


@app.route('/savetousb', methods=["POST"])
@requires_auth
def savetousb():
    """
    moves files in the 'upload_dir' specified in the config file, to a disk.

    this will only work to move files to /dev/sda1.

    :return: whether the transfer was a success
    :rtype: str
    """
    config = ConfigParser()
    name = request.form.get("name", None)
    if not name:
        abort(500)
    config.read(os.path.join("configs_byserial", name + '.ini'))
    try:
        subprocess.call("mount /dev/sda1 /mnt/", shell=True)
        shutil.copytree(config["localfiles"]["upload_dir"], os.path.join("/mnt/", config["camera"]["name"]))
    except Exception as e:
        subprocess.call("umount /mnt", shell=True)
        print(str(e))
        return "failure"
    return "success"


def after_this_request(func):
    """
    Call after request helper.

    :param types.FunctionType func: function to call
    :return: provided function
    :rtype: types.FunctionType
    """
    if not hasattr(g, 'call_after_request'):
        g.call_after_request = []
    g.call_after_request.append(func)
    return func


@app.after_request
def per_request_callbacks(response):
    """
    I have no idea what this does but it looks important

    :param response: ???
    :return: ???
    """
    for func in getattr(g, 'call_after_request', ()):
        response = func(response)
    return response


def shutdown_server():
    """
    Shuts down the webinterface

    """
    func = request.environ.get('werkzeug.server.shutdown')
    if func is None:
        raise RuntimeError('Not running with the Werkzeug Server')
    func()


@app.route('/restart')
@app.route('/reboot')
@requires_auth
def restart():
    """
    Restarts the raspberry pi through `reboot now` system call.

    Probably unsafe but whatever.

    :return: Response about rebooting
    :rtype: Response
    """

    @after_this_request
    def sd(response):
        """
        After request callback to reboot the pi
        I dont think this works.... maybe I'm wrong.
        """
        print("SHUTTING DOWN!")
        time.sleep(1)
        os.system("reboot now")
        return response

    return "Rebooting... ", 200


@app.route("/update")
@requires_auth
def update():
    """
    Pulls the current version of SPC-eyepi from github, and replaces the one running with it.

    :return: string response indicating success.
    :rtype: str
    """

    @after_this_request
    def update(response):
        app.debug = False
        os.system("git fetch --all;git reset --hard origin/master")
        os.system("systemctl restart spc-eyepi_capture.service")
        return response

    app.debug = True
    return "SUCCESS"


@app.route("/update_to_tag/<tag>")
@requires_auth
def update_tag(tag: str):
    """
    the same as update, except this can take a git tag to update to.

    :param str tag: git tag to update to.
    :return: string response indicating success.
    :rtype: str
    """

    @after_this_request
    def update(response):
        app.debug = False
        os.system("git fetch --tags --all;git reset --hard {}".format(tag))
        os.system("systemctl restart eyepi-capture.service")
        return response

    app.debug = True
    return "SUCCESS"


@app.route("/pip_install")
@requires_auth
def pip_install():
    """
    installs a package using pip.

    Doesnt reload/restart anything, so after this is called, python needs to be restarted.

    :return: string response indicating success.
    :rtype: str
    """
    import pip
    _, package = dict(request.args).popitem()
    pip.main(["install", package])
    return "SUCCESS"


@app.route("/wificonfig", methods=['POST'])
@requires_auth
def wificonfig():
    """
    wifi configuration view.
    Accepts a POST request with 'ssid' and 'key' which are used to create a netctl profile.

    TODO: alter this to work with netctl-auto rather than vanilla netctl.

    :return: string response indicating success or 400 response if request type is not POST.
    :rtype: str
    """
    if request.method == 'POST':
        interface = os.popen("ip link | cut -c4- | grep ^w | sed 's/:.*//'").read().rstrip()
        print("Interface: " + interface)
        ssid = request.form["ssid"]
        key = request.form["key"]

        with open('/etc/netctl/netprofile', 'w') as netprofile:
            netprofile.write(render_template("netprofile", interface=interface, ssid=ssid, key=key))

        print("Stopping AP")
        os.system("systemctl stop create_ap.service")
        print("Putting interface down")
        os.system("ifconfig " + interface + " down")
        if os.system("netctl start netprofile") != 0:
            print("Connection failed restarting AP")
            print("\nLog:\n\n" + os.popen("systemctl status netctl@netprofile.service").read())
            os.system("systemctl start create_ap.service")
        return "success"
    else:
        return abort(400)


@app.route("/newuser", methods=['POST'])
@requires_auth
def newuser():
    """
    POST endpoint for adding a user.

    Accepts 'username', 'pass', 'adminpass' form arguments.

    Password has a minimum of 5 chars.

    todo: this should be moved to :mod:`api`, and should be jsonified like a real api.

    :return: string response indicating success or 400 response if request type is not POST.
    :rtype: str
    """
    if request.method == 'POST':
        username = request.form["username"]
        password = request.form["pass"]
        adminpass = request.form.get("adminpass", None)
        if len(username) > 0 and len(password) > 5:
            return "success" if add_user(username, password, adminpass) else "auth_error"
        else:
            return "invalid"
    else:
        return abort(400)


@app.route('/admin')
@requires_auth
def admin():
    """
    Administration page view
    """
    db = dbm.open('db', 'r')
    usernames = []
    k = db.firstkey()
    while k is not None:
        usernames.append(k)
        k = db.nextkey(k)

    return render_template("admin.html", usernames=usernames)


@app.route('/update_camera/<path:serialnumber>', methods=["GET", "POST"])
@requires_auth
def update_camera_config(serialnumber: str):
    """
    Update camera config endpoint.

    Exists to update the cameras configuration.

    Parses many values.

    :param serialnumber: serialnumber of the camera to update
    :return: response indicating result of operation
    :rtype: Response
    """

    ser = None

    config_map = {
        'name': ('camera', 'name'),
        'capture': ('camera', 'enabled'),
        'upload': ('ftp', 'enabled'),
        'username': ('ftp', 'username'),
        'password': ('ftp', 'password'),
        'server': ('ftp', 'server'),
        'timestamp': ('ftp', 'timestamp'),
        'replace': ('ftp', 'replace'),
        'interval': ('timelapse', 'interval'),
        'starttime': ('timelapse', 'starttime'),
        'stoptime': ('timelapse', 'stoptime')
    }
    tf = {"True": "on", "False": "off"}
    if request.method == "POST":
        config = ConfigParser()
        with open("/etc/machine-id") as f:
            m_id = str(f.read())
            m_id = m_id.strip('\n')
        if m_id == serialnumber:
            # modify picam file if machine id is the sn
            config_path = "picam.ini"
            config.read(config_path)
            for key, value in request.form.items(multi=True):
                if value in tf.keys():
                    # parse datetimes correctly, because they are gonna be messy.
                    if value in ["starttime", "stoptime"]:
                        dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.Z")
                        value = dt.strftime('%H:%M')
                    value = tf[value]
                config[config_map[key][0]][config_map[key][1]] = value
            try:
                sanitizeconfig(config, config_path)
                return "", 200
            except Exception as e:
                "", 500

        if os.path.isfile(os.path.join("configs_byserial", serialnumber + ".ini")):
            # modify camera by serial if available, otherwise 404.
            config_path = os.path.join("configs_byserial", serialnumber + ".ini")
            config.read(config_path)
            for key, value in request.form.items(multi=True):
                if value in tf.keys():
                    value = tf[value]
                config[config_map[key][0]][config_map[key][1]] = value
            try:
                sanitizeconfig(config, config_path)
                return "", 200
            except Exception as e:
                "", 500
        else:
            return "", 404
    return "", 405


@app.route("/fix_configs")
@requires_auth
def fix_confs():
    """
    Edits all config files, removing any sections that arent in example.ini

    todo: this is unneccesary, and what the hell is going on with the return? ???

    :return: string response of ???
    """
    configs = {}
    default = ConfigParser()
    default.read("example.ini")
    defaultsections = set(default.sections())
    confs = glob("configs_byserial/*.ini")
    returnvalues = []
    confs.append("picam.ini")
    confs.append("eyepi.ini")
    for conff in confs:
        configs[conff] = ConfigParser()
        configs[conff].read(conff)

    for path, config in configs.items():
        for section in set(configs[conff].sections()) - defaultsections:
            a = config.remove_section(section)
            returnvalues.append(section + "?" + str(a))
        with open(conff, 'wb') as configfile:
            config.write(configfile)
    return a.join("--")


@app.route("/command", methods=["POST"])
@requires_auth
def run_command() -> str:
    """
    runs arbitrary commands from post data.

    form_key: value
    command: space separayed list of arguments

    :return: str, json response of each command and the corresponding stdout (and stderr).
    :rtype: str
    """

    response = {}
    for command, argument in request.form.items():
        try:
            a = subprocess.check_output([" ".join([command, argument])], stderr=subprocess.STDOUT,
                                        shell=True).decode()
            response[command] = str(a)
        except Exception as e:
            response[command] = {}
            response[command]['exc'] = str(e)
            if hasattr(e, "output"):
                response[command]['out'] = str(e.output.decode())
    return str(json.dumps(response))


@app.route("/reset_machine_id")
@requires_auth
def reset_machine_id():
    """
    Resets the machine-id to a random one generated by 'systemd-machine-id-setup'.

    :return: str, json response {ERR: "error message"} if an error occurs, otherwise {}
    :rtype: str
    """
    resp = {}
    print("Resetting machine ID")
    try:
        if os.path.isfile("/etc/machine-id"):
            os.remove("/etc/machine-id")
        os.system("systemd-machine-id-setup")
    except Exception as e:
        resp["ERR"] = str(e)
    return str(json.dumps(resp))


@app.route('/net')
@requires_auth
def network():
    """
    network view
    """
    return render_template("network.html")


def trunc_at(s: str, d: str, n: int) -> str:
    """
    :param str s: string to truncate
    :param str d: delimiter
    :param int n: number of occurrences to ignore before caring
    :return: s truncated at the n'th occurrence of the delimiter, d.
    :rtype: str
    """
    return d.join(s.split(d)[:n])


def get_net_size(netmask):
    binary_str = ''
    for octet in netmask:
        binary_str += bin(int(octet))[2:].zfill(8)
    return str(len(binary_str.rstrip('0')))


def commit_ip(ipaddress: str = None, subnet: str = None, gateway: str = None, dev="eth0"):
    # this is blank on purpose. It needs fixing so its not so shit.


def make_dynamic(dev: str):
    """
    disable static ip addressing using systemctl.

    Dont use this, it is probably broken.

    :param str dev: device to change (eth0, wlp3s0 etc).
    """
    os.system("systemctl disable network@{}".format(dev))


def set_ip(ipaddress: str=None, subnet: str=None, gateway: str=None, dev: str="eth0"):
    """
    sets a static ip address manually to the device specified.


    :param ipaddress: ip address to commit
    :param subnet: subnet to user
    :param gateway: gateway to template
    :param dev: device to use TODO: actually make this do something
    """
    if ipaddress is not None and subnet is not None and gateway is not None:
        os.system("ip addr add {}/{} broadcast {}.255 dev {}".format(ipaddress, get_net_size(subnet),
                                                                     trunc_at(ipaddress, ".", 3), dev))
        os.system("ip route add default via " + gateway)
    else:
        make_dynamic(dev)


@app.route('/set-ip', methods=['POST'])
@requires_auth
def set_ips():
    """
    POST endpoing for setting a static ip (or enabling dhcp).

    the form accepts "ip-form-dynamic" as an on off flag to enable/disable dhcp.

    Otherwise it requires ip-form-ipaddress, ip-form-subnet and ip-form-gateway to set the IP to them.

    :return: strin response indicating success
    :rtype: str
    """
    try:
        if "ip-form-dynamic" in request.form.keys():
            if request.form['ip-form-dynamic'] == "on":
                set_ip()
            else:
                return "fail"
        else:
            try:
                socket.inet_aton(request.form["ip-form-ipaddress"])
                socket.inet_aton(request.form["ip-form-subnet"])
                socket.inet_aton(request.form["ip-form-gateway"])
                set_ip(ipaddress=request.form["ip-form-ipaddress"],
                          subnet=request.form["ip-form-subnet"],
                          gateway=request.form["ip-form-gateway"])
                return 'success'
            except Exception as e:
                return "fail"
    except:
        return "fail"


@app.route('/commit-ip', methods=['POST'])
@requires_auth
def commit_ip_():
    if request.method == 'POST':
        try:
            if "ip-form-dynamic" in request.form.keys():
                if request.form['ip-form-dynamic'] == "on":
                    set_ip()
                else:
                    return "fail"
            else:
                try:
                    socket.inet_aton(request.form["ip-form-ipaddress"])
                    socket.inet_aton(request.form["ip-form-subnet"])
                    socket.inet_aton(request.form["ip-form-gateway"])
                    set_ip(ipaddress=request.form["ip-form-ipaddress"],
                           subnet=request.form["ip-form-subnet"],
                           gateway=request.form["ip-form-gateway"])

                    return 'success'
                except Exception as e:
                    return "fail"
        except:
            return "fail"
    else:
        abort(400)


@app.route('/break_the_interface')
@requires_auth
def break_the_interface():
    """
    Intentionally load a nonexistent template to start the
    `werkezeug interactive debugger <http://werkzeug.pocoo.org/docs/0.11/debug/>`_
    """
    return render_template("bljdg.html")


@app.route('/delcfg', methods=['POST'])
@requires_auth
def delcfg():
    """
    deletes a configuration file from the config directory.

    :return: string response indicating success or failure
    :rtype: str
    """
    try:
        os.remove(os.path.join("configs_byserial", request.form["name"] + ".ini"))
        return "success"
    except:
        return "FAILURE"


@app.route('/writecfg', methods=['POST'])
@requires_auth
def writecfg():
    """
    Writes the data contained within the post form to a configuration file.

    :return: string response indicating success or failure or 400 Response if something broke writing the config
    :rtype: str or Response
    """

    aconfig = ConfigParser()
    config_name = request.form["config-name"] + ".ini"
    if not config_name == "picam.ini":
        config_path = os.path.join("configs_byserial", config_name)
    else:
        config_path = config_name

    aconfig.read(config_path)
    # this is required because the default behaviour of checkboxes is that they do not trigger if they are unchecked.
    aconfig["camera"]["enabled"] = "off"
    aconfig["ftp"]["upload"] = "off"
    aconfig["ftp"]["replace"] = "off"
    aconfig["ftp"]["timestamp"] = "off"
    for key, value in request.form.items(multi=True):
        # print"key:" + key +"  value:"+value
        if value != "" and key != "config-name":
            sect = key.split('.')[0]
            opt = key.split(".")[1]
            aconfig[sect][opt] = value
            # print("changed: " + sect + ':' + opt + ':' + value)
    try:
        sanitizeconfig(aconfig, config_path)
        return "success"
    except Exception as e:
        abort(400)


@app.route('/change_hostname', methods=['POST'])
@requires_auth
def change_hostname():
    """
    Changes the hostname on the machine, including the /etc/hosts file.
    Also goes through all the configuration files looking for the old hostname.

    :return: string response indicating success or 400 response if form is incomplete or if writing files failed.
    :rtype: str or Response
    """
    if not request.form.get('hostname', None):
        abort(400)
    hostname = request.form['hostname']
    config = ConfigParser()
    config_path = "eyepi.ini"
    config.read(config_path)
    config["camera"]["name"] = hostname
    pi_config = ConfigParser()
    pi_config_path = "picam.ini"
    pi_config.read(config_path)
    pi_config["camera"]["name"] = hostname + "-Picam"
    try:
        with open("/etc/hosts", 'w') as hostsfile:
            hostsfile.write(render_template('hosts.j2', hostname=hostname))

        with open("/etc/hostname", 'w') as hostnamefile:
            hostnamefile.write(hostname + '\n')
        os.system("hostname " + hostname)
    except Exception as e:
        print("Something went horribly wrong")
        print(str(e))
        abort(500)

    try:
        sanitizeconfig(config, config_path)
        sanitizeconfig(pi_config, pi_config_path)
        return "success"
    except Exception as e:
        abort(500)

@app.route('/')
@requires_auth
def config():
    """
    Index view.

    Configuration page for Cameras.
    """
    example = ConfigParser()
    rpiconfig = ConfigParser()
    example.read("example.ini")
    configs = {}
    for file in glob(os.path.join("configs_byserial", "*.ini")):
        configs[os.path.basename(file)[:-4]] = ConfigParser()
        configs[os.path.basename(file)[:-4]].read(file)
    return render_template("config.html", configs=configs, example=example)


@app.route('/filemanagement')
@requires_auth
def filemanagement():
    """
    file management view

    Information about the system storage status per block device, allows transfer of files to usb etc.
    """
    fsinfo = [line.split() for line in subprocess.check_output("df -h", shell=True).decode().splitlines()]
    return render_template("filemgmt.html", fsinfo=fsinfo)


@app.route("/images")
def images():
    """
    Image preview view.

    Allows the capture of a new image and replace in the client
    """

    example = ConfigParser()
    example.read("example.ini")

    configs = {}
    for file in glob(os.path.join("configs_byserial", "*.ini")):
        configs[os.path.basename(file)[:-4]] = ConfigParser()
        configs[os.path.basename(file)[:-4]].read(file)
    urls = []
    #
    # TODO: this is hella broken. need to find a better way of doing this....
    for file in glob(os.path.join("static", "temp", "*.jpg")):
        urls.append(os.path.basename(file)[:-4])
    return render_template("images.html", configs=configs, image_urls=urls, example=example)


@app.route("/getfilteredlog", methods=["POST"])
@requires_auth
def getfilteredlog():
    """
    API enpoint for getting a filtered log view.

    TODO: move this to :mod:`api`

    """
    if request.method == 'POST':
        query = request.form["query"].lower()
        returnstring = ''
        with open("spc-eyepi.log", 'r') as f:
            f.seek(0, 2)
            fsize = f.tell()
            f.seek(max(fsize - 10.24 ** 6, 0), 0)
            lines = f.readlines()
            a = reversed(lines)
        for line in a:
            if fnmatch.fnmatch(line.lower(), "*" + query.lower() + "*") and len(returnstring.splitlines()) < 250:
                returnstring += "<tr><td>" + line + "</td></tr>" + '\n'
        returnstring += "<tr><td><h3>Truncated at 250 lines of 1Mb into the past</h3></td></tr>"
        return returnstring
    else:
        abort(400)


@app.route('/log/<lt>/<lc>')
def stream(lt, lc):
    """
    log line streaming endpoint

    streams lines that contain lt until a count of lc is reached.

    todo: Continue searching through archived logs.

    TODO: move this to :mod:`api`

    :param str lt: query to match in the log
    :param str lc: number of results
    :return:
    """
    def generate():
        line = 1
        with open("spc-eyepi.log") as f:
            r = "     "
            while len(r):
                if line > int(lc):
                    break
                line += 1
                r = f.readline().strip()

                if lt.lower() in r.lower():
                    yield r + "\n"

    return Response(generate(), mimetype='text/plain')


def gen(camera)->bytes:
    """
    Video streaming generator function.

    Intentionally limited to 10fps to account for bad quality connection and slow hardware.

    this should be used as the argument for a :class:`Response` along with the mimetype
    `multipart/x-mixed-replace; boundary=frame` to ensure that the browser correctly replaces the previous frame,
    and knows to continue to stream.

    :return: image frame as encoded bytes, almost a complete response.
    :rtype: bytes
    """
    while True:
        time.sleep(0.1)
        frame = camera.get_frame()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')


@app.route('/pi_feed')
def pi_feed():
    """
    Video streaming route for the raspberry pi camera.

    Put this in the src attribute of an img tag.

    :return: streamed image Response or emptystring
    :rtype: Response or str
    """
    try:
        cam = PiCamera("asdfhjgasdkf", noconf=True)
        return Response(gen(cam), mimetype='multipart/x-mixed-replace; boundary=frame')
    except Exception as e:
        print("exception:" + str(e))
        return ""


@app.route('/ivport_switch/<int:cam_num>')
def ivport_switch(cam_num)->str:
    """
    Switch the current ivport picamera thread

    :param int cam_num: index to switch to.
    :return: string of the camera number that was switched to
    :rtype: str
    """
    IVPortCamera.switch(idx=cam_num)
    return str(cam_num)


@app.route('/ivport_feed/<int:cam_num>')
def ivport_feed(cam_num):
    """
    Streaming from a specific picamera with the IVPort multiplexer

    Actually calls :func:`pi_feed`, because the IVPOrt multiplexer works on the picamera.

    :return: streamed image Response or emptystring (as per pi_feed)
    :rtype: Response or str
    """
    IVPortCamera.switch(idx=cam_num)
    return pi_feed()


@app.route("/logfile")
@requires_auth
def logfile():
    """
    log view function

    TODO: do we have like 3 log page view functions? collect them into one.
    """
    return render_template("logpage.html")


@app.route("/<any('css','js'):selector>/<path:path>")
@requires_auth
def get_resource(selector, path):
    """
    serves css and js files from the static directory

    :param selector: type of resource ("js" or "css")
    :param path: name/path of the file.
    :return: file Response
    :rtype: Response
    """
    return send_from_directory("static", filename=os.path.join(selector, path))


if os.system("ping -c 1 8.8.8.8") != 0:
    # setup the access point if we cannot get online.
    setup_ap()
    def cleanup():
        """
        stops create_ap service on exit.
        """
        # stop the access point when we exit.
        os.system("systemctl stop create_ap.service")
        print("create_ap stopped gracefully")

    import atexit
    # register the exit callback
    atexit.register(cleanup)

# dispatchermiddleware to run bothe the app, and browsepy mount at the same time.
application = DispatcherMiddleware(app, mounts={
    "/filesystem": browsepy.app
})

if __name__ == "__main__":
    run_simple("0.0.0.0", 80, application, use_debugger=True, use_reloader=True, threaded=True)
