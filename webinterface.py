#!/usr/bin/python
import socket, os, hashlib, subprocess
import Crypto.Protocol.KDF
import anydbm
import datetime, re, fnmatch
from glob import glob
from functools import wraps
from flask import Flask, redirect, url_for, request, send_file, abort, Response, render_template, jsonify
from ConfigParser import SafeConfigParser

config_filename = 'eyepi.ini'
otherconfig_filename = 'picam.ini'
example_filename = 'example.ini'

global piname
piname = "SPC-eyepi"

app = Flask(__name__, static_url_path='/static')
app.debug = True

def sanitizeconfig(towriteconfig, filename):
    print "do checking here"
    with open(filename, 'wb') as configfile:
        towriteconfig.write(configfile)

def geteosserialnumber(port):
    try:
        cmdret = subprocess.check_output('gphoto2 --port "'+port+'" --get-config eosserialnumber', shell=True)
        return cmdret[cmdret.find("Current: ")+9: len(cmdret)-1]
    except:
        return 0

def create_config(serialnumber, eosserial = 0):
    if not os.path.exists("configs_byserial"):
       os.makedirs("configs_byserial")
    thiscfg = SafeConfigParser()
    thiscfg.read("eyepi.ini")
    thiscfg.set("localfiles","spooling_dir",os.path.join(thiscfg.get("localfiles","spooling_dir"),serialnumber))
    thiscfg.set("localfiles","upload_dir",os.path.join(thiscfg.get("localfiles","upload_dir"),serialnumber))
    thiscfg.set("camera","name",thiscfg.get("camera","name") +"-"+serialnumber)
    thiscfg.set("eosserialnumber","value", eosserial)
    with open(os.path.join("configs_byserial",serialnumber+'.ini'), 'wb') as configfile:
        thiscfg.write(configfile)

def detect_cameras(type):
    try:
        a = subprocess.check_output("gphoto2 --auto-detect", shell=True)
        cams = {}
        for port in re.finditer("usb:", a):
            cmdret = subprocess.check_output('gphoto2 --port "'+a[port.start():port.end()+7]+'" --get-config serialnumber', shell=True)
            cams[a[port.start():port.end()+7]] = cmdret[cmdret.find("Current: ")+9: len(cmdret)-1]
        return cams
    except Exception as e:
        print str(e)
        
def check_auth(username, password):
    db = anydbm.open('db', 'r')
    if str(username) in db:
        m = Crypto.Protocol.KDF.PBKDF2(password=str(password),salt=str(username),count=100)
        if m==db[str(username)]:
            db.close()
            return True
        else:
            return False
    db.close()

@app.errorhandler(404)
def not_found(error):
    return render_template('page_not_found.html'), 404

@app.errorhandler(500)
def server_error(error):
    return render_template('server_error.html'), 500

@app.errorhandler(401)
def bad_auth(error):
    return render_template('bad_auth.html'), 401

def add_user(username, password, adminpass):
    hash = Crypto.Protocol.KDF.PBKDF2(password=str(password),salt=str(username),count=100)
    adminpasshash = Crypto.Protocol.KDF.PBKDF2(password=str(password),salt="admin",count=100)
    db = anydbm.open('db', 'c')
    # later only allow users control over their own password and admin to add later.
    if str(username) not in db:
        if adminpasshash == db["admin"]:
           db[str(username)] = hash
        else:
            return False
    else:
        if adminpasshash == db["admin"] or adminpasshash==db[str(username)]:
            db[str(username)] = hash
        else:
            return False
    return True

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth=request.authorization
        if not auth or not check_auth(auth.username,auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated
        
def authenticate():
    return Response('Access DENIED!',401,{'WWW-Authenticate':'Basic realm="Login Required"'})

@app.route("/imgs/<path:path>")
def get_image(path):
    if '..' in path or path.startswith('/'):
        abort(404)
    return send_file(os.path.join("static","temp",path+".jpg"))


@app.route('/rotatelogfile')
@requires_auth
def rotatelogfile():
    config = SafeConfigParser()
    config.read(config_filename)
    config2 = SafeConfigParser()
    config2.read(otherconfig_filename)
    if config.get("ftp","uploaderenabled")=="on":
        with open("static/logfile.txt",'r') as log:
            with open(os.path.join(config.get("localfiles","upload_dir"),datetime.datetime.now().strftime('%Y_%m_%d_%H_%M_%S')+".log"),'w' ) as writeout:
                writeout.write(log.read())
    elif config2.get("ftp","uploaderenabled")=="on":
        with open("static/logfile.txt",'r') as log:
            with open(os.path.join(config2.get("localfiles","upload_dir"),datetime.datetime.now().strftime('%Y_%m_%d_%H_%M_%S')+".log"),'w' ) as writeout:
                writeout.write(log.read())

    open("static/logfile.txt","w").close()
    return redirect(url_for('logfile'))

@app.route('/restart')
@requires_auth
def restart():
    print "shutting down"
    os.system("reboot")
    return redirect(url_for('admin'))

@app.route("/update")
@requires_auth
def update():
    os.system("git fetch --all")
    os.system("git reset --hard origin/master")
    return redirect(url_for('admin'))#'<html><head><script type="text/javascript" //function(){document.location.reload(true);},60000);</script></head><body>UPDATING!! WAIT PLEASE!!</body></html>'

@app.route("/newuser", methods=['POST'])
@requires_auth
def newuser():
    #if request.method == 'POST':
    username = request.form["username"]
    password = request.form["password"]
    adminpass = request.form["adminpass"]
    print username + password + adminpass
    if len(username)>0 and len(password)>5:
        if add_user(username, password, adminpass) == True:
            return "success"
        else:
            return "auth_error"
    else:
         return "invalid"
    #else:
    #    abort(400)

@app.route('/admin')
@requires_auth
def admin():
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
    db = anydbm.open('db', 'r')
    usernames = []
    for key,value in db.iteritems():
        usernames.append(key)
    return render_template("admin.html", version=version, usernames=usernames)

@app.route('/net')
@requires_auth
def network():
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
    try:
        if os.path.isfile("interfaces"):
            netcfg = open("interfaces",'r')
        else:
            os.symlink("/etc/network/interfaces")
            netcfg = open("interfaces",'r')
    except:
        abort(500)
    return render_template("network.html", version=version, netcfg = netcfg)

@app.route('/savenet', methods=['POST'])
@requires_auth
def savenet():
    if request.method == 'POST':
        try:
            with open("interfaces",'w') as file:
                file.write(request.form["interfaces"])
            return "success"
        except: 
            abort(500)
    else:
        abort(400)

@app.route('/delcfg', methods=['POST'])
@requires_auth
def delcfg():
    if request.method =='POST':
        try:
            os.remove(os.path.join("configs_byserial",request.form["name"]+".ini"))
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
            for port, serial_number in cameras.iteritems():
                if not os.path.isfile(os.path.join("configs_byserial", serial_number+".ini")):
                    eos_serial = geteosserialnumber(port)
                    create_config(serial_number, eos_serial)
                    post_return_string+="Added new config for S#" + (serial_number if eos_serial==0 else eos_serial) +"<br>"
            return post_return_string
        except Exception as e:
            return "Something went horribly wrong! :"+str(e)

@app.route('/writecfg', methods=['POST'])
@requires_auth
def writecfg():
    if request.method == 'POST':
        aconfig = SafeConfigParser()
        config_name=request.form["config-name"]+".ini"
        if not config_name == "picam.ini":
            config_path = os.path.join("configs_byserial",config_name) 
        else:
            config_path = config_name
                         
        aconfig.read(config_path)
        aconfig.set("camera","enabled","off")
        aconfig.set("ftp","uploaderenabled","off")
        aconfig.set("ftp","uploadwebcam","off")
        aconfig.set("ftp","uploadtimestamped","off")
        for key, value in request.form.iteritems(multi=True):
            #print "key:" + key +"  value:"+value
            if value != "" and key != "config-name":
                sect = key.split('.')[0]
                opt = key.split(".")[1]
                aconfig.set(sect,opt,value)
                print "changed: "+sect+':'+opt+':'+value
        try:
            sanitizeconfig(aconfig, config_path)
            return "success"
        except Exception as e:
            abort(400)

@app.route('/')
@requires_auth
def config():
    example = SafeConfigParser()
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
    
    rpiconfig = SafeConfigParser()
    rpiconfig.read("picam.ini")
    configs = {}
    for file in glob(os.path.join("configs_byserial","*.ini")):
        configs[os.path.basename(file)[:-4]] = SafeConfigParser()
        configs[os.path.basename(file)[:-4]].read(file)
    return render_template("config.html", version=version, configs = configs, rpiconfig = rpiconfig)

@app.route('/filemanagement')
@requires_auth
def filemanagement():
    a = subprocess.check_output("df -h", shell=True)
    fsinfolines = a.splitlines()
    fsinfo = []
    for line in fsinfolines:
        fsinfo.append(line.split())
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
    rpiconfig = SafeConfigParser()
    rpiconfig.read("picam.ini")
    configs = {}
    filelists = {}
    for file in glob(os.path.join("configs_byserial","*.ini")):
        configs[os.path.basename(file)[:-4]] = SafeConfigParser()
        configs[os.path.basename(file)[:-4]].read(file)
        thisglob = glob(os.path.join(configs[os.path.basename(file)[:-4]].get("localfiles","upload_dir"),"*.*"))[-1000:]
        dictglob = {}
        for path in thisglob:
            dictglob[os.path.basename(path)] = path
        filelists[os.path.basename(file)[:-4]] = dictglob

    
    filelists["picam"] = glob(os.path.join(rpiconfig.get("localfiles","upload_dir"),"*.*"))[-1000:]    
    return render_template("filemgmt.html",version=version, fsinfo = fsinfo, configs=configs, rpiconfig=rpiconfig, filelists=filelists)


@app.route('/filelist', methods=['POST'])
@requires_auth
def filelist():
    if request.method == 'POST':
        config = SafeConfigParser()
        config_name=request.form["name"]+".ini"
        if not config_name == "picam.ini":
            config_path = os.path.join("configs_byserial",config_name) 
        else:
            config_path = config_name
                         
        config.read(config_path)
        list=glob(os.path.join(config.get("localfiles","upload_dir"),"*.*") )
        if len(list) > 1000:
            return jsonify(results=list[-1000:])
        else:
            return jsonify(results=list)
    else:
        abort(400)
        #return request.form["name"]
        

@app.route("/images")
def images():
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
    configs = {}
    rpiconfig = SafeConfigParser()
    rpiconfig.read("picam.ini")
    for file in glob(os.path.join("configs_byserial","*.ini")):
        configs[os.path.basename(file)[:-4]] = SafeConfigParser()
        configs[os.path.basename(file)[:-4]].read(file)
    urls = []
    for file in glob(os.path.join("static","temp","*.jpg")):
        urls.append(os.path.basename(file)[:-4])
    return render_template("images.html", version=version, configs=configs, rpiconfig=rpiconfig, image_urls=urls)

@app.route("/getfilteredlog", methods=["POST"])
@requires_auth
def getfilteredlog():
    if request.method == 'POST':
        query = request.form["query"].lower()
        returnstring = ''
        
        with open("static/logfile.txt",'r') as file:
            istoolong = False
            lines=[]
            for line in file:
                lines.append(line.strip() + '<br>')
                
            for line in reversed(lines):
                if fnmatch.fnmatch(line.lower(),"*"+query.lower()+"*") and len(returnstring.splitlines()) <= 500:
                    returnstring += "<tr><td>"+line+"</td></tr>"+'\n'
            if len(returnstring.splitlines())==500:
                returnstring+="<tr><td><h3>Truncated at 500 results</h3></td></tr>"
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
        for key, value in request.form.iteritems(multi=True):
            if value == "on" and not any(x in os.path.dirname(key) for x in
                                         ["/bin","/dev","/mnt","/proc","/run","/srv","/tmp","/var","/boot","/etc","/lib","/opt","/root","/sbin","/sys","/usr"]):
                os.remove(key)
            else:
                retstr="DO NOT DELETE THINGS YOU SHOULDNT!!! GRRRR!"
        return retstr
    else:
        abort(400)

@app.route("/logfile")
@requires_auth
def logfile():
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
    return render_template("logpage.html", version=version)

if __name__ == "__main__":
    app.run(host='0.0.0.0')

