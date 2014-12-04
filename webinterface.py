#!/usr/bin/python
import socket, os, hashlib, subprocess
import Crypto.Protocol.KDF
import anydbm
import datetime
from functools import wraps
from flask import Flask, redirect, url_for, request, send_file, abort, Response
from ConfigParser import SafeConfigParser

config_filename = 'eyepi.ini'
otherconfig_filename = 'picam.ini'
example_filename = 'example.ini'

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
    with open("static/logfile.txt",'r') as log:
        with open(os.path.join(config.get("localfiles","upload_dir"),datetime.datetime.now().strftime('%Y_%m_%d_%H_%M_%S')+".log"),'w' ) as writeout:
            writeout.write(log.read())

    open("static/logfile.txt","w").close()
    return redirect(url_for('logfile'))

@app.route('/restart')
@requires_auth
def restart():
    print "shutting down"
    os.system("reboot")
    return redirect(url_for('index'))

@app.route("/update")
@requires_auth
def update():
    os.system("git fetch --all")
    os.system("git reset --hard origin/master")
    return '<html><head><script type="text/javascript" function(){document.location.reload(true);},60000);</script></head><body>UPDATING!! WAIT PLEASE!!</body></html>'

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


def createform(position, configfile, example):
    returnstring = "<script type='text/javascript'>function sa(s){var d=document.getElementById('h'+s);d.style.display=d.style.display!='none'?'none':'block';};</script> \
<div style='background-color:#2E9AFE; padding:1%;border:2px solid; box-shadow:0px 0px 30px black;margin:5px;border-radius:3px;width:45%;float:"+position+";'>"
    returnstring +="<h3>"+configfile +"</h3>"
    if position == "left":returnstring += "<a href="+ url_for('lastimage')+">"+"LAST IMAGE</a>"
    else: returnstring += "<a href="+ url_for('lastpicam')+">"+"LAST IMAGE</a>"
    returnstring += "<form action=/"+position+" method=POST><button>SUBMIT</button>"

    config = SafeConfigParser()
    config.read(configfile)
    for section in config.sections():
        if section == "mqtt":
            returnstring += "<br><button onclick=\"sa('"+position+"');return false;\">Advanced options</button>"
            returnstring += "<div id='h"+position+"' style='display:none;'><h4>"+section+"</h4>"
        else:
            returnstring += "<div><h4>"+section+"</h4>"
        for option in config.options(section):
            try:
                if option == "enabled" or option == "uploadwebcam" or option == "uploadtimestamped" or option == "uploaderenabled":
                    returnstring += option + "<input type='checkbox' name="+section+'.'+option+" placeholder="+config.get(section, option)
                    if config.get(section, option) == "on":
                        returnstring += " checked><br>"
                    else:
                        returnstring += " ><br>"
                else:    
                    returnstring += option + "<input type='text' title=example:"+example.get(section,option)+" name="+section+'.'+option+" placeholder="+config.get(section, option)+" ><br>"
            except Exception as e:
                returnstring += option + "<input type='text' placeholder='NO!' >"
            
            #strsect = str(section)
            #print strsect+","+strit
            #opti = config.get(strsect,strit)
	    #print opti
        if section != "mqtt":
            returnstring += "</div>"
    returnstring += "</div></form></div>"
    return returnstring

@app.route('/right', methods=['GET','POST'])
@requires_auth
def right():
    aconfig = SafeConfigParser()
    aconfig.read(otherconfig_filename)
    aconfig.set("camera","enabled","off")
    aconfig.set("ftp","uploaderenabled","off")
    aconfig.set("ftp","uploadwebcam","off")
    aconfig.set("ftp","uploadtimestamped","off")
    if request.method == 'POST':
        for key, value in request.form.iteritems(multi=True):
            print "key:" + key +"  value:"+value
            if value != "":
                sect = key.split('.')[0]
                opt = key.split(".")[1]
                aconfig.set(sect,opt,value)
                print "changed: "+sect+':'+opt+':'+value
        try:
            sanitizeconfig(aconfig, otherconfig_filename)
            return redirect(url_for('index'))
        except Exception as e:
            abort(400)

@app.route('/left', methods=['GET','POST'])
@requires_auth
def left():
    aconfig = SafeConfigParser()
    aconfig.read(config_filename)
    aconfig.set("camera","enabled","off")
    aconfig.set("ftp","uploaderenabled","off")
    aconfig.set("ftp","uploadwebcam","off")
    aconfig.set("ftp","uploadtimestamped","off")
    if request.method == 'POST':
        for key, value in request.form.iteritems(multi=True):
            if value != "":
                sect = key.split('.')[0]
                opt = key.split(".")[1] 
                aconfig.set(sect,opt,value)
                print "changed: "+sect+':'+opt+':'+value
        try:
            sanitizeconfig(aconfig, config_filename)
            return redirect(url_for('index'))
        except Exception as e:
            abort(400)

@app.route('/', methods=['GET','POST'])
@requires_auth
def index():
    example = SafeConfigParser()
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
    example.read(example_filename)
    returnstring = "<html><head><link rel='shortcut icon' href='/static/favicon.ico' type='image/x-icon'> <link rel='icon' href='/static/favicon.ico' type='image/x-icon'></head> \
<body style='margin-left:auto;margin-right:auto;color:yellow;width:98%;font-family:\"Times New Roman\"\, Times, serif;' bgcolor=\"#0000FF\"><div style='display:block;'><img src='/static/fpimg.png' style='float:left;width:10%;'></img><h1 style='display:inline;float:left;width:79%;'><marquee behaviour='alternate'>Configuration Page for "+socket.gethostname()+"</marquee></h1><img src='/static/fpimg.png' style='float:right;width:10%;'></img></div>\
<br><br><form style='text-align:center;' action=restart><br><button>REBOOT</button></form>\
<br><p style='display:block;text-align:center;'>version "+version+"</p>\
<br><a style='text-align:center;display:block;' href="+ url_for('logfile')+">LOG</a><br>\
<br><br><form style='text-align:center;' action=update><br><button>UPDATE</button></form>"
    returnstring += createform("left", config_filename, example)
    returnstring += createform("right", otherconfig_filename, example)
    returnstring += "</body></html>"
    return returnstring

@app.route("/lastimage")
def lastimage():
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
    returnstring = "<html><head><script type='text/javascript' src='//ajax.googleapis.com/ajax/libs/jquery/1.11.1/jquery.min.js'></script><link rel='shortcut icon' href='/static/favicon.ico' type='image/x-icon'> <link rel='icon' href='/static/favicon.ico' type='image/x-icon'></head> \
<body style='color:yellow;width:100%;font-family:\"Times New Roman\"\, Times, serif;' bgcolor=\"#0000FF\"><div style='display:block;'><img src='/static/fpimg.png' style='float:left;width:10%;'></img><h1 style='display:inline;float:left;width:79%;'><marquee behaviour='alternate'>Configuration Page for "+socket.gethostname()+"</marquee></h1><img src='/static/fpimg.png' style='float:right;width:10%;'></img></div>\
<br><br><form style='text-align:center;' action=rotatelogfile><button>ROTATE</button></form><br>"
    returnstring += "<br><br><div id='changeme'></div>"
    returnstring += "<script type='text/javascript'>\
window.setInterval(function(){\
 $.get('getlog').then(function(responseData){$('#changeme').html(responseData);});\
},2000);\
</script>"
    returnstring += "</body></html>"
    return returnstring

if __name__ == "__main__":
    app.run(host='0.0.0.0')

