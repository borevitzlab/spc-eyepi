#!/usr/bin/python3
from __future__ import print_function
from __future__ import division
import socket
import os
import subprocess
import re
import fnmatch
import shutil
import time
import json
from datetime import datetime
from glob import glob
from functools import wraps
import logging

import Crypto.Protocol.KDF
from flask import Flask, redirect, url_for, request, send_file, abort, Response, render_template, jsonify, \
    send_from_directory

from six.moves import dbm
from six.moves.configparser import ConfigParser

config_filename = 'eyepi.ini'
otherconfig_filename = 'picam.ini'
example_filename = 'example.ini'

app = Flask(__name__, static_url_path='/static')
app.debug = True
kmsghandler = logging.FileHandler("/dev/kmsg", 'w')
app.logger.addHandler(kmsghandler)


def sanitizeconfig(towriteconfig, filename):
    print("do checking here")
    with open(filename, 'wb') as configfile:
        towriteconfig.write(configfile)


def get_time():
    return str(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))


app.jinja_env.globals.update(get_time=get_time)


def get_hostname():
    return str(socket.gethostname())


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
    with open(os.path.join("configs_byserial", serialnumber + '.ini'), 'wb') as configfile:
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
    db = dbm.open('db', 'r')
    if str(username) in db:
        m = Crypto.Protocol.KDF.PBKDF2(password=str(password), salt=str(username), count=100)
        if m == db[str(username)]:
            db.close()
            return True
        else:
            return False
    db.close()


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
    hash = Crypto.Protocol.KDF.PBKDF2(password=str(password_to_set), salt=str(username), count=100)
    adminpasshash = Crypto.Protocol.KDF.PBKDF2(password=str(adminpass), salt="admin", count=100)
    db = dbm.open('db', 'c')
    # later only allow users control over their own password and admin to add later.
    # allow global admin password to change everything.
    if adminpasshash == db["admin"]:
        db[str(username)] = hash
        db.close()
        return True

    # for each username, only allow the correct hash to change the password
    for username_, hash_ in db.items():
        if username_ == username and adminpasshash == db[str(username)]:
            db[str(username)] = hash
            db.close()
            return True

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
                serialnumber) + ".jpg'", shell=True)
        print(a)
        return False
    except subprocess.CalledProcessError as e:
        print(e.output)
        return True


def capture_preview(serialnumber):
    try:
        a = subprocess.check_output("gphoto2 --auto-detect", shell=True)
        for port in re.finditer("usb:", a):
            cmdret = subprocess.check_output(
                'gphoto2 --port "' + a[port.start():port.end() + 7] + '" --get-config serialnumber', shell=True)
            _serialnumber = cmdret[cmdret.find("Current: ") + 9: len(cmdret) - 1]
            port = a[port.start():port.end() + 7]
            if _serialnumber == serialnumber:
                tries = 0
                while tries < 10 and cap_lock_wait(port, serialnumber):
                    tries += 1
                    time.sleep(1)
                return True

    except subprocess.CalledProcessError as e:
        print(str(e))


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


"""
          d8                                                                     88                               ad88  88  88              
        ,8P'                             ,d                   ,d                 88                              d8"    ""  88              
       d8"                               88                   88                 88                              88         88              
     ,8P'     8b,dPPYba,   ,adPPYba,   MM88MMM  ,adPPYYba,  MM88MMM   ,adPPYba,  88   ,adPPYba,    ,adPPYb,d8  MM88MMM  88  88   ,adPPYba,  
    d8"       88P'   "Y8  a8"     "8a    88     ""     `Y8    88     a8P_____88  88  a8"     "8a  a8"    `Y88    88     88  88  a8P_____88  
  ,8P'        88          8b       d8    88     ,adPPPPP88    88     8PP"""""""  88  8b       d8  8b       88    88     88  88  8PP"""""""  
 d8"          88          "8a,   ,a8"    88,    88,    ,88    88,    "8b,   ,aa  88  "8a,   ,a8"  "8a,   ,d88    88     88  88  "8b,   ,aa  
8P'           88           `"YbbdP"'     "Y888  `"8bbdP"Y8    "Y888   `"Ybbd8"'  88   `"YbbdP"'    `"YbbdP"Y8    88     88  88   `"Ybbd8"'  
                                                                                                   aa,    ,88                               
                                                                                                    "Y8bbdP"
"""


@app.route('/rotatelogfile')
@requires_auth
def rotatelogfile():
    config = ConfigParser()
    config.read(config_filename)
    config2 = ConfigParser()
    config2.read(otherconfig_filename)
    if config["ftp"]["uploaderenabled"] == "on":
        with open("static/logfile.txt", 'r') as log:
            with open(os.path.join(config["localfiles"]["upload_dir"],
                                   datetime.datetime.now().strftime('%Y_%m_%d_%H_%M_%S') + ".log"), 'w') as writeout:
                writeout.write(log.read())
    elif config2["ftp"]["uploaderenabled"] == "on":
        with open("static/logfile.txt", 'r') as log:
            with open(os.path.join(config2["localfiles"]["upload_dir"],
                                   datetime.datetime.now().strftime('%Y_%m_%d_%H_%M_%S') + ".log"), 'w') as writeout:
                writeout.write(log.read())

    open("static/logfile.txt", "w").close()
    return redirect(url_for('logfile'))


"""
          d8                                                                                                88           
        ,8P'                                                    ,d                                          88           
       d8"                                                      88                                          88           
     ,8P'     ,adPPYba,  ,adPPYYba,  8b       d8   ,adPPYba,  MM88MMM   ,adPPYba,   88       88  ,adPPYba,  88,dPPYba,   
    d8"       I8[    ""  ""     `Y8  `8b     d8'  a8P_____88    88     a8"     "8a  88       88  I8[    ""  88P'    "8a  
  ,8P'         `"Y8ba,   ,adPPPPP88   `8b   d8'   8PP"""""""    88     8b       d8  88       88   `"Y8ba,   88       d8  
 d8"          aa    ]8I  88,    ,88    `8b,d8'    "8b,   ,aa    88,    "8a,   ,a8"  "8a,   ,a88  aa    ]8I  88b,   ,a8"  
8P'           `"YbbdP"'  `"8bbdP"Y8      "8"       `"Ybbd8"'    "Y888   `"YbbdP"'    `"YbbdP'Y8  `"YbbdP"'  8Y"Ybbd8"'                                                                                                                       
"""


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


"""
          d8                                                                               
        ,8P'                                       ,d                               ,d     
       d8"                                         88                               88     
     ,8P'     8b,dPPYba,   ,adPPYba,  ,adPPYba,  MM88MMM  ,adPPYYba,  8b,dPPYba,  MM88MMM  
    d8"       88P'   "Y8  a8P_____88  I8[    ""    88     ""     `Y8  88P'   "Y8    88     
  ,8P'        88          8PP"""""""   `"Y8ba,     88     ,adPPPPP88  88            88     
 d8"          88          "8b,   ,aa  aa    ]8I    88,    88,    ,88  88            88,    
8P'           88           `"Ybbd8"'  `"YbbdP"'    "Y888  `"8bbdP"Y8  88            "Y888                                                                                         
"""


@app.route('/restart')
@requires_auth
def restart():
    print("shutting down")
    try:
        os.system("reboot")
    except Exception as e:
        return render_template('server_error.html'), 500
    return redirect(url_for('admin'))


@app.route("/update")
@requires_auth
def update():
    os.system("git fetch --all")
    os.system("git reset --hard origin/master")
    return "SUCCESS"


@app.route("/status")
@requires_auth
def status():
    return ''


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
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
    db = dbm.open('db', 'r')
    usernames = []
    for key, value in db.items():
        usernames.append(key)
    return render_template("admin.html", version=version, usernames=usernames)


@app.route('/update_camera/<path:serialnumber>', methods=["GET", "POST"])
@requires_auth
def update_camera_config(serialnumber):
    ser = None

    config_map = {
        'name': ('camera', 'name'),
        'camera_enabled': ('camera', 'enabled'),
        'upload_enabled': ('ftp', 'uploaderenabled'),
        'upload_username': ('ftp', 'user'),
        'upload_pass': ('ftp', 'pass'),
        'upload_server': ('ftp', 'server'),
        'upload_timestamped': ('ftp', 'uploadtimestamped'),
        'upload_webcam': ('ftp', 'uploadwebcam'),
        'interval_in_seconds': ('timelapse', 'interval'),
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


@app.route('/botnetmgmt')
@requires_auth
def botnetmgmt():
    # use post later to send commands
    # get hostname:
    jsondata = {}
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
    jsondata["version"] = version.strip("\n")
    hn = None
    try:
        with open("/etc/hostname", "r") as fn:
            hn = fn.readlines()[0]
        metadatas = {}
        metadatas_from_cameras_fn = glob("*.json")
        for fn in metadatas_from_cameras_fn:
            with open(fn, 'r') as f:
                metadatas[os.path.splitext(fn)[0]] = json.loads(f.read())

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
        jsondata['metadata'] = metadatas
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


@app.route("/command", methods=["GET", "POST"])
@requires_auth
def run_command():
    """
        accepts arbitrary commands as post, and only post
        accepts as command1:argument1 argument2, command2: argument1 argument2 ...
    """
    if request.method == 'POST':
        response = {}
        for command, argument in request.form.keys():
            try:
                os.system(" ".join([command, argument]))
                response[command] = "OK"
            except Exception as e:
                response[command] = str(e)
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
    try:
        if os.path.isfile("/ect/machine-id"):
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
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
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
        aconfig["camera"]["enabled"] = "off"
        aconfig["ftp"]["uploaderenabled"] = "off"
        aconfig["ftp"]["uploadwebcam"] = "off"
        aconfig["ftp"]["uploadtimestamped"] = "off"
        for key, value in request.form.items(multi=True):
            # print"key:" + key +"  value:"+value
            if value != "" and key != "config-name":
                sect = key.split('.')[0]
                opt = key.split(".")[1]
                aconfig[sect][opt] = value
                print("changed: " + sect + ':' + opt + ':' + value)
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
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
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
    a = subprocess.check_output("df -h", shell=True)
    fsinfolines = a.splitlines()
    fsinfo = []
    for line in fsinfolines:
        fsinfo.append(line.split())
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
    rpiconfig = ConfigParser()
    rpiconfig.read("picam.ini")
    configs = {}
    filelists = {}
    for file in glob(os.path.join("configs_byserial", "*.ini")):
        configs[os.path.basename(file)[:-4]] = ConfigParser()
        configs[os.path.basename(file)[:-4]].read(file)
        thisglob = glob(os.path.join(configs[os.path.basename(file)[:-4]]["localfiles"]["upload_dir"], "*.*"))[
                   -1000:]
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
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
    example = ConfigParser()
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
    example.read("example.ini")

    configs = {}
    rpiconfig = ConfigParser()
    rpiconfig.read("picam.ini")
    for file in glob(os.path.join("configs_byserial", "*.ini")):
        configs[os.path.basename(file)[:-4]] = ConfigParser()
        configs[os.path.basename(file)[:-4]].read(file)
    urls = []
    for file in glob(os.path.join("static", "temp", "*.jpg")):
        urls.append(os.path.basename(file)[:-4])
    return render_template("images.html", version=version, configs=configs, rpiconfig=rpiconfig, image_urls=urls,
                           example=example)


@app.route("/getfilteredlog", methods=["POST"])
@requires_auth
def getfilteredlog():
    if request.method == 'POST':
        query = request.form["query"].lower()
        returnstring = ''
        with open("spc-eyepi.log", 'rb') as f:
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
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
    return render_template("logpage.html", version=version)


@app.route("/<any('css','js'):selector>/<path:path>")
@requires_auth
def get_resource(selector, path):
    return send_from_directory("static", filename=os.path.join(selector, path))


if __name__ == "__main__":
    app.run(host='0.0.0.0')
