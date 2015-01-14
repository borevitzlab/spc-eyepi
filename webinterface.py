#!/usr/bin/python
import socket, os, hashlib, subprocess
import Crypto.Protocol.KDF
import anydbm
import datetime
from glob import glob
from functools import wraps
from flask import Flask, redirect, url_for, request, send_file, abort, Response, render_template
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

def add_user(username, password):
    hash = Crypto.Protocol.KDF.PBKDF2(password=str(password),salt=str(username),count=100)
    db = anydbm.open('db', 'c')
    if str(username) not in db:
        db[str(username)] = hash
        return True
    return False

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
    return redirect(url_for('cameras'))

@app.route("/update")
@requires_auth
def update():
    os.system("git fetch --all")
    os.system("git reset --hard origin/master")
    return redirect(url_for('cameras'))#'<html><head><script type="text/javascript" //function(){document.location.reload(true);},60000);</script></head><body>UPDATING!! WAIT PLEASE!!</body></html>'

@app.route("/adduser", methods=['GET','POST'])
@requires_auth
def adduser():
    returnstring = "<html><head><link rel='shortcut icon' href='/static/favicon.ico' type='image/x-icon'> <link rel='icon' href='/static/favicon.ico' type='image/x-icon'></head>\
<body style='color:yellow;width:100%;font-family:\"Times New Roman\"\, Times, serif;' bgcolor=\"#0000FF\"><div style='display:block;'><img src='/static/fpimg.png' style='float:left;width:10%;'></img><h1 style='display:inline;float:left;width:79%;'><marquee behaviour='alternate'>Configuration Page for "+socket.gethostname()+"</marquee></h1><img src='/static/fpimg.png' style='float:right;width:10%;'></img></div>\
<br><br>"
    returnstring+="<form action=/adduser method=POST>"
    returnstring+="<br><input type='text' name='username' placeholder='username:'>"
    returnstring+="<br><input type='password' name='password' placeholder='password:'>"
    returnstring += "<button>SUBMIT</button></form>"
    if request.method == 'POST':
        username = None
        password = None
        for key, value in request.form.iteritems(multi=True):
            print "key:" + key + "   value: "+ value
            if key == "username":
                username = value
            if key == "password":
                password = value
        if len(username)>0 and len(password)>5:
            if add_user(username, password):
                returnstring += "SUCCESS"
            else:
                returnstring += "Something went wrong. Are you trying to change a user that already exists?"
        else:
            returnstring+="<p>you didnt enter something, try again<p>"
            

    returnstring += "</body></html>"
    return returnstring

@app.route('/delcfg', methods=['POST'])
@requires_auth
def delcfg():
    if request.method =='POST':
        try:
            os.remove(os.path.join("configs_byserial",request.form["name"]+".ini"))
            return "success"
        except:
            return "FAILURE"


@app.route('/writecfg', methods=['POST'])
@requires_auth
def writecfg():

    if request.method == 'POST':
        aconfig = SafeConfigParser()
        
        aconfig.read(os.path.join("configs_byserial",request.form["config-name"]+".ini"))
        aconfig.set("camera","enabled","off")
        aconfig.set("ftp","uploaderenabled","off")
        aconfig.set("ftp","uploadwebcam","off")
        aconfig.set("ftp","uploadtimestamped","off")
        for key, value in request.form.iteritems(multi=True):
            print "key:" + key +"  value:"+value
            if value != "" and key != "config-name":
                sect = key.split('.')[0]
                opt = key.split(".")[1]
                aconfig.set(sect,opt,value)
                print "changed: "+sect+':'+opt+':'+value
        try:
            sanitizeconfig(aconfig, os.path.join("configs_byserial",request.form["config-name"]+".ini"))
            return "success"
        except Exception as e:
            abort(400)

@app.route('/', methods=['GET','POST'])
@requires_auth
def config():
    example = SafeConfigParser()
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
    configs = {}
    for file in glob(os.path.join("configs_byserial","*.ini")):
        configs[os.path.basename(file)[:-4]] = SafeConfigParser()
        configs[os.path.basename(file)[:-4]].read(file)
    return render_template("config.html", version=version, configs = configs)

@app.route("/lastimage")
def lastimage():
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
    config = SafeConfigParser()
    config.read(config_filename)
    return '<META HTTP-EQUIV="EXPIRES" CONTENT="Mon, 22 Jul, 2002 12:00:00 GMT"><html>\
<script type="text/javascript">window.setTimeout(function(){document.location.reload(true);},'+str(float(config.get("timelapse","interval"))*1000)+');</script>\
<body><img src='+ url_for("static",filename="temp/dslr_last_image.jpg")+'></img></body></html>'

@app.route("/dslr_last_image.jpg")
def DSLR_last_image():
    if os.path.isfile("static/temp/dslr_last_image.jpg"):
        return send_file("static/temp/dslr_last_image.jpg")
    else:
        abort(404)

@app.route("/pi_last_image.jpg")
def PI_last_image():
    if os.path.isfile("static/temp/pi_last_image.jpg"):
        return send_file("static/temp/pi_last_image.jpg")
    else:
        abort(404)

@app.route("/lastpicam")
def lastpicam():
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
    config = SafeConfigParser()
    config.read(otherconfig_filename)
    return '<META HTTP-EQUIV="EXPIRES" CONTENT="Mon, 22 Jul, 2002 12:00:00 GMT"><html>\
<script type="text/javascript">window.setTimeout(function(){document.location.reload(true);},'+str(float(config.get("timelapse","interval"))*1000)+');</script>\
<body><img src='+ url_for("static",filename="temp/pi_last_image.jpg")+'></img></body></html>'

@app.route("/getlog")
@requires_auth
def getlog():
    returnstring = ""
    with open("static/logfile.txt",'r') as file:
        lines=[]
        for line in file:
            lines.append(line.strip() + '<br>')
        for line in reversed(lines):
            returnstring += line
    return returnstring

@app.route("/logfile")
@requires_auth
def logfile():
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
    return render_template("logpage.html", version=version)

if __name__ == "__main__":
    app.run(host='0.0.0.0')

