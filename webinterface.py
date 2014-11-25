#!/usr/bin/python
import socket, os
from flask import Flask, redirect, url_for, request
from ConfigParser import SafeConfigParser

config_filename = 'eyepi.ini'
otherconfig_filename = 'picam.ini'
example_filename = 'example.ini'

app = Flask(__name__)
app.debug = True

@app.route('/restart')
def restart():
    print "shutting down"
    os.system("reboot")
    return redirect(url_for('index'))

def createform(position, configfile, example):
    returnstring = "<div style='border:2px solid;border-radius:3px;width:49%;float:"+position+";'>"
    returnstring +="<h3>"+configfile +"</h3>"
    if position == "left":returnstring += "<a href="+ url_for('lastimage')+">"+"LAST IMAGE</a>"
    else: returnstring += "<a href="+ url_for('lastpicam')+">"+"LAST IMAGE</a>"
    returnstring += "<form action=/"+position+" method=POST><button>SUBMIT</button>"

    config = SafeConfigParser()
    config.read(configfile)
    for section in config.sections():
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
        returnstring += "</div>"
    returnstring += "</form></div>"
    return returnstring

@app.route('/right', methods=['GET','POST'])
def right():
    aconfig = SafeConfigParser()
    aconfig.read(otherconfig_filename)
    aconfig.set("camera","enabled","off")
    aconfig.set("uploaderenabled","enabled","off")
    aconfig.set("uploadwebcam","enabled","off")
    aconfig.set("uploadtimestamped","enabled","off")
    if request.method == 'POST':
        for key, value in request.form.iteritems(multi=True):
            print "key:" + key +"  value:"+value
            if value != "":
                sect = key.split('.')[0]
                opt = key.split(".")[1]
                aconfig.set(sect,opt,value)
                print "changed: "+sect+':'+opt+':'+value
        with open(otherconfig_filename, 'wb') as configfile:
            aconfig.write(configfile)
    print "returning right"
    return redirect(url_for('index'))

@app.route('/left', methods=['GET','POST'])
def left():
    aconfig = SafeConfigParser()
    aconfig.read(config_filename)
    aconfig.set("camera","enabled","off")
    if request.method == 'POST':
        for key, value in request.form.iteritems(multi=True):
            if value != "":
                sect = key.split('.')[0]
                opt = key.split(".")[1]
                aconfig.set(sect,opt,value)
                print "changed: "+sect+':'+opt+':'+value
        with open(config_filename, 'wb') as configfile:
            aconfig.write(configfile)
    print "returning left"
    return redirect(url_for('index'))

    
@app.route('/', methods=['GET','POST'])
def index():
    example = SafeConfigParser()
    example.read(example_filename)
    returnstring = "<html><body><h1>Configuration Page for "+socket.gethostname()+"</h1><form action=restart><button>REBOOT</button></form><br>"
    returnstring += createform("left", config_filename, example)
    returnstring += createform("right", otherconfig_filename, example)
    return returnstring

@app.route("/lastimage")
def lastimage():
    config = SafeConfigParser()
    config.read(config_filename)
    return '<META HTTP-EQUIV="EXPIRES" CONTENT="Mon, 22 Jul, 2002 12:00:00 GMT"><script type="text/javascript">window.setTimeout(function(){document.location.reload(true);},'+str(float(config.get("timelapse","interval"))*1000)+');</script><div style="background-image:url('+ url_for('static',filename='dslr_last_image.jpg') + ');width:100%;height:100%;background-size:cover;"></div>'


@app.route("/lastpicam")
def lastpicam():
    config = SafeConfigParser()
    config.read(otherconfig_filename)
    return '<META HTTP-EQUIV="EXPIRES" CONTENT="Mon, 22 Jul, 2002 12:00:00 GMT"><script type="text/javascript">window.setTimeout(function(){document.location.reload(true);},'+str(float(config.get("timelapse","interval"))*1000)+');</script><div style="background-image:url('+ url_for('static',filename='pi_last_image.jpg') + ');width:100%;height:100%;background-size:cover;"></div>'

if __name__ == "__main__":
    app.run(host='0.0.0.0')

