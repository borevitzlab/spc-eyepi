#!/usr/bin/python
import socket, os, hashlib
import Crypto.Protocol.KDF
import anydbm
from functools import wraps
from flask import Flask, redirect, url_for, request, send_file, abort, Response
from ConfigParser import SafeConfigParser

config_filename = 'eyepi.ini'
otherconfig_filename = 'picam.ini'
example_filename = 'example.ini'

app = Flask(__name__)
app.debug = True

def sanitizeconfig(towriteconfig):
    print "do checking here"
    with open(config_filename, 'wb') as configfile:
        towriteconfig.write(configfile)
        
def check_auth(username, password):
    db = anydbm.open('db', 'r')
    m = Crypto.Protocol.KDF.PBKDF2(password=str(password),salt=str(username),count=10000)
    if m==db[username]:
        return True
        db.close()
    db.close()

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth=request.authorization
        if not auth or not check_auth(auth.username,auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated
        
def authenticate():
    return Response('Access DENIED!, Please log in:',401,{'WWW-Authenticate':'Basic realm="Login Required"'})


@app.route('/restart')
def restart():
            #
    print "shutting down"
    os.system("reboot")
    return redirect(url_for('index'))

def createform(position, configfile, example):
    returnstring = "<script type='text/javascript'> function sa(s){var d=document.getElementById('h'+s);d.style.display=d.style.display!='none'?'none':'block';};</script> <div style='padding:10px;border:2px solid;border-radius:3px;width:47%;float:"+position+";'>"
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
            sanitizeconfig(aconfig)
            return redirect(url_for('index'))
        except Exception as e:
            abort(400)

@app.route('/left', methods=['GET','POST'])
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
            sanitizeconfig(aconfig)
            return redirect(url_for('index'))
        except Exception as e:
            abort(400)

    
@app.route('/', methods=['GET','POST'])
@requires_auth
def index():
    example = SafeConfigParser()
    example.read(example_filename)
    returnstring = "<html><body><h1><marquee behaviour='alternate'>Configuration Page for "+socket.gethostname()+"</marquee></h1><form action=restart><button>REBOOT</button></form><br>"
    returnstring += createform("left", config_filename, example)
    returnstring += createform("right", otherconfig_filename, example)
    return returnstring

@app.route("/lastimage")
def lastimage():
    config = SafeConfigParser()
    config.read(config_filename)
    return '<META HTTP-EQUIV="EXPIRES" CONTENT="Mon, 22 Jul, 2002 12:00:00 GMT"><html><script type="text/javascript">window.setTimeout(function(){document.location.reload(true);},'+str(float(config.get("timelapse","interval"))*1000)+');</script><body><img src='+ url_for("static",filename="dslr_last_image.jpg")+'></img></body></html>'

@app.route("/dslr_last_image.jpg")
def DSLR_last_image():
    if os.path.isfile("static/dslr_last_image.jpg"):
        return send_file("static/dslr_last_image.jpg")
    else:
        abort(404)

@app.route("/pi_last_image.jpg")
def PI_last_image():
    if os.path.isfile("static/pi_last_image.jpg"):
        return send_file("static/pi_last_image.jpg")
    else:
        abort(404)

@app.route("/lastpicam")
def lastpicam():
    config = SafeConfigParser()
    config.read(otherconfig_filename)
    return '<META HTTP-EQUIV="EXPIRES" CONTENT="Mon, 22 Jul, 2002 12:00:00 GMT"><script type="text/javascript">window.setTimeout(function(){document.location.reload(true);},'+str(float(config.get("timelapse","interval"))*1000)+');</script><img src="' + url_for("static",filename="pi_last_image.jpg")+'></div>'


if __name__ == "__main__":
    app.run(host='0.0.0.0')

