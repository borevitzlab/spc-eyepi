import dbm
import json
import os
import subprocess
from configparser import ConfigParser
from functools import wraps
from glob import glob
import urllib, socket

from flask import Flask, Response, request
from flask.ext.bcrypt import Bcrypt

from libs.AESCipher import AESCipher

try:
    # generate a new machine id if one does not already exist
    torrc = """Log notice syslog
                DataDirectory /var/lib/tor
                HiddenServiceDir /home/tor_private/
                HiddenServicePort 80 127.0.0.1:5000
                HiddenServicePort 6666 127.0.0.1:666
                HiddenServiceAuthorizeClient basic bvz"""

    # with open('/etc/tor/torrc', 'w') as f:
    #     f.write("\n".join(q.lstrip("                ") for q in torrc.splitlines()))

    if not os.path.exists("/etc/machine-id"):
        os.system("systemd-machine-id-setup")

    os.system("chown -R tor:tor /home/tor_private ")
    os.system("chown -R tor:tor /var/lib/tor")
except:
    print("something went wrong, oh well...")

config_filename = 'eyepi.ini'
otherconfig_filename = 'picam.ini'
example_filename = 'example.ini'

app = Flask(__name__)
app.debug = True
bcrypt = Bcrypt(app)


cfg = ConfigParser()
cfg.read("picam.ini")
try:
    encryptdb = urllib.request.urlopen("http://data.phenocam.org.au/p.ejson")
    a = AESCipher(cfg['ftp']['pass'])
    f = json.loads(a.decrypt(encryptdb.read()))
    with dbm.open('db', 'c') as db:
        db[b'admin'] = bcrypt.generate_password_hash(f['admin'])
except Exception as e:
    print("something broke decrypting the new db{}".format(str(e)))


def authenticate():
    return Response('Access DENIED!', 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})

from flask import jsonify
def as_json(f):
    def decorated(*args, **kwargs):
        @wraps(f)
        def fn(*args, **kwargs):
            return jsonify(f(*args, *kwargs))
        return fn
    return decorated

@app.route("/")
@as_json
def test():
    return {"asdasd":"sadfasfd",1:3333}

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        with dbm.open('db', 'r') as passdb:
            auth_allowed = bcrypt.check_password_hash(passdb[b'admin'].decode('utf-8'), auth.password)
        if not auth_allowed:
            return authenticate()
        return f(*args, **kwargs)
    return decorated


@app.route('/botnetmgmt')
@requires_auth
def botnetmgmt():
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
                    urllib.request.urlopen('https://api.ipify.org/?format=json', timeout=10).read().decode('utf-8'))[
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
    app.run(host='0.0.0.0', port=666)
