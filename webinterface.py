#!/usr/bin/python2
from __future__ import print_function
from __future__ import division
import socket, os, hashlib, subprocess
import Crypto.Protocol.KDF
import anydbm
import datetime, re, fnmatch, shutil,time, json
import cPickle
import copy
from datetime import datetime
from glob import glob
from functools import wraps
from flask import Flask, redirect, url_for, request, send_file, abort, Response, render_template, jsonify, send_from_directory
from ConfigParser import SafeConfigParser

config_filename = 'eyepi.ini'
otherconfig_filename = 'picam.ini'
example_filename = 'example.ini'

app = Flask(__name__, static_url_path='/static')
app.debug = True

"""             
                                    88           88                                                                  ad88  88               
                                    ""    ,d     ""                                                                 d8"    ""               
                                          88                                                                        88                      
,adPPYba,  ,adPPYYba,  8b,dPPYba,   88  MM88MMM  88  888888888   ,adPPYba,   ,adPPYba,   ,adPPYba,   8b,dPPYba,   MM88MMM  88   ,adPPYb,d8  
I8[    ""  ""     `Y8  88P'   `"8a  88    88     88       a8P"  a8P_____88  a8"     ""  a8"     "8a  88P'   `"8a    88     88  a8"    `Y88  
 `"Y8ba,   ,adPPPPP88  88       88  88    88     88    ,d8P'    8PP"""""""  8b          8b       d8  88       88    88     88  8b       88  
aa    ]8I  88,    ,88  88       88  88    88,    88  ,d8"       "8b,   ,aa  "8a,   ,aa  "8a,   ,a8"  88       88    88     88  "8a,   ,d88  
`"YbbdP"'  `"8bbdP"Y8  88       88  88    "Y888  88  888888888   `"Ybbd8"'   `"Ybbd8"'   `"YbbdP"'   88       88    88     88   `"YbbdP"Y8  
                                                                                                                                aa,    ,88  
                                                                                                                                 "Y8bbdP"    
"""
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


"""                                                                                                                                                                          
                                                                                                         88              88                                                88                                   
                           ,d                                                                            ""              88                                                88                                   
                           88                                                                                            88                                                88                                   
 ,adPPYb,d8   ,adPPYba,  MM88MMM   ,adPPYba,   ,adPPYba,   ,adPPYba,  ,adPPYba,   ,adPPYba,  8b,dPPYba,  88  ,adPPYYba,  88  8b,dPPYba,   88       88  88,dPYba,,adPYba,   88,dPPYba,    ,adPPYba,  8b,dPPYba,  
a8"    `Y88  a8P_____88    88     a8P_____88  a8"     "8a  I8[    ""  I8[    ""  a8P_____88  88P'   "Y8  88  ""     `Y8  88  88P'   `"8a  88       88  88P'   "88"    "8a  88P'    "8a  a8P_____88  88P'   "Y8  
8b       88  8PP"""""""    88     8PP"""""""  8b       d8   `"Y8ba,    `"Y8ba,   8PP"""""""  88          88  ,adPPPPP88  88  88       88  88       88  88      88      88  88       d8  8PP"""""""  88          
"8a,   ,d88  "8b,   ,aa    88,    "8b,   ,aa  "8a,   ,a8"  aa    ]8I  aa    ]8I  "8b,   ,aa  88          88  88,    ,88  88  88       88  "8a,   ,a88  88      88      88  88b,   ,a8"  "8b,   ,aa  88          
 `"YbbdP"Y8   `"Ybbd8"'    "Y888   `"Ybbd8"'   `"YbbdP"'   `"YbbdP"'  `"YbbdP"'   `"Ybbd8"'  88          88  `"8bbdP"Y8  88  88       88   `"YbbdP'Y8  88      88      88  8Y"Ybbd8"'    `"Ybbd8"'  88          
 aa,    ,88                                                                                                                                                                                                     
  "Y8bbdP"                                                                                                                                                                 																																															  
"""
def geteosserialnumber(port):
	try:
		cmdret = subprocess.check_output('gphoto2 --port "'+port+'" --get-config eosserialnumber', shell=True)
		return cmdret[cmdret.find("Current: ")+9: len(cmdret)-1]
	except:
		return 0
"""                                                                        
                                                                                                                            ad88  88               
                                                  ,d                                                                       d8"    ""               
                                                  88                                                                       88                      
 ,adPPYba,  8b,dPPYba,   ,adPPYba,  ,adPPYYba,  MM88MMM   ,adPPYba,                 ,adPPYba,   ,adPPYba,   8b,dPPYba,   MM88MMM  88   ,adPPYb,d8  
a8"     ""  88P'   "Y8  a8P_____88  ""     `Y8    88     a8P_____88                a8"     ""  a8"     "8a  88P'   `"8a    88     88  a8"    `Y88  
8b          88          8PP"""""""  ,adPPPPP88    88     8PP"""""""                8b          8b       d8  88       88    88     88  8b       88  
"8a,   ,aa  88          "8b,   ,aa  88,    ,88    88,    "8b,   ,aa                "8a,   ,aa  "8a,   ,a8"  88       88    88     88  "8a,   ,d88  
 `"Ybbd8"'  88           `"Ybbd8"'  `"8bbdP"Y8    "Y888   `"Ybbd8"'                 `"Ybbd8"'   `"YbbdP"'   88       88    88     88   `"YbbdP"Y8  
                                                                                                                                       aa,    ,88  
                                                                     888888888888                                                       "Y8bbdP"   
"""
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
"""                                                                                                                                                               
         88                                                                                                                                                                 
         88                ,d                               ,d                                                                                                              
         88                88                               88                                                                                                              
 ,adPPYb,88   ,adPPYba,  MM88MMM   ,adPPYba,   ,adPPYba,  MM88MMM                 ,adPPYba,  ,adPPYYba,  88,dPYba,,adPYba,    ,adPPYba,  8b,dPPYba,  ,adPPYYba,  ,adPPYba,  
a8"    `Y88  a8P_____88    88     a8P_____88  a8"     ""    88                   a8"     ""  ""     `Y8  88P'   "88"    "8a  a8P_____88  88P'   "Y8  ""     `Y8  I8[    ""  
8b       88  8PP"""""""    88     8PP"""""""  8b            88                   8b          ,adPPPPP88  88      88      88  8PP"""""""  88          ,adPPPPP88   `"Y8ba,   
"8a,   ,d88  "8b,   ,aa    88,    "8b,   ,aa  "8a,   ,aa    88,                  "8a,   ,aa  88,    ,88  88      88      88  "8b,   ,aa  88          88,    ,88  aa    ]8I  
 `"8bbdP"Y8   `"Ybbd8"'    "Y888   `"Ybbd8"'   `"Ybbd8"'    "Y888                 `"Ybbd8"'  `"8bbdP"Y8  88      88      88   `"Ybbd8"'  88          `"8bbdP"Y8  `"YbbdP"'  
                                                                                                                                                                            
                                                                   888888888888                                                                                             
"""
def detect_cameras(type):
	try:
		a = subprocess.check_output("gphoto2 --auto-detect", shell=True)
		cams = {}
		for port in re.finditer("usb:", a):
			cmdret = subprocess.check_output('gphoto2 --port "'+a[port.start():port.end()+7]+'" --get-config serialnumber', shell=True)
			cams[a[port.start():port.end()+7]] = cmdret[cmdret.find("Current: ")+9: len(cmdret)-1]
		return cams
	except Exception as e:
		print(str(e))



"""                                                                                                        
            88                                   88                                                         88           
            88                                   88                                                  ,d     88           
            88                                   88                                                  88     88           
 ,adPPYba,  88,dPPYba,    ,adPPYba,   ,adPPYba,  88   ,d8                 ,adPPYYba,  88       88  MM88MMM  88,dPPYba,   
a8"     ""  88P'    "8a  a8P_____88  a8"     ""  88 ,a8"                  ""     `Y8  88       88    88     88P'    "8a  
8b          88       88  8PP"""""""  8b          8888[                    ,adPPPPP88  88       88    88     88       88  
"8a,   ,aa  88       88  "8b,   ,aa  "8a,   ,aa  88`"Yba,                 88,    ,88  "8a,   ,a88    88,    88       88  
 `"Ybbd8"'  88       88   `"Ybbd8"'   `"Ybbd8"'  88   `Y8a                `"8bbdP"Y8   `"YbbdP'Y8    "Y888  88       88  
                                                                                                                         
                                                            888888888888                                                 
"""	   
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

"""                               
                                                             
 ,adPPYba,  8b,dPPYba,  8b,dPPYba,   ,adPPYba,   8b,dPPYba,  
a8P_____88  88P'   "Y8  88P'   "Y8  a8"     "8a  88P'   "Y8  
8PP"""""""  88          88          8b       d8  88          
"8b,   ,aa  88          88          "8a,   ,a8"  88          
 `"Ybbd8"'  88          88           `"YbbdP"'   88                                                                  
"""
@app.errorhandler(404)
def not_found(error):
	return render_template('page_not_found.html'), 404
"""

"""
@app.errorhandler(500)
def server_error(error):
	return render_template('server_error.html'), 500
"""

"""
@app.errorhandler(401)
def bad_auth(error):
	return render_template('bad_auth.html'), 401




"""                                                                              
                     88           88                                                                
                     88           88                                                                
                     88           88                                                                
,adPPYYba,   ,adPPYb,88   ,adPPYb,88                88       88  ,adPPYba,   ,adPPYba,  8b,dPPYba,  
""     `Y8  a8"    `Y88  a8"    `Y88                88       88  I8[    ""  a8P_____88  88P'   "Y8  
,adPPPPP88  8b       88  8b       88                88       88   `"Y8ba,   8PP"""""""  88          
88,    ,88  "8a,   ,d88  "8a,   ,d88                "8a,   ,a88  aa    ]8I  "8b,   ,aa  88          
`"8bbdP"Y8   `"8bbdP"Y8   `"8bbdP"Y8                 `"YbbdP'Y8  `"YbbdP"'   `"Ybbd8"'  88          
                                                                                                    
                                      888888888888                                                  
"""
def add_user(username, password_to_set, adminpass):
	hash = Crypto.Protocol.KDF.PBKDF2(password=str(password_to_set),salt=str(username),count=100)
	adminpasshash = Crypto.Protocol.KDF.PBKDF2(password=str(adminpass),salt="admin",count=100)
	db = anydbm.open('db', 'c')
	# later only allow users control over their own password and admin to add later.

	# allow global admin password to change everything.
	if adminpasshash == db["admin"]:
		db[str(username)] = hash
		db.close()
		return True

	# for each username, only allow the correct hash to change the password
	for username_, hash_ in db.iteritems():
		if username_ == username and adminpasshash == db[str(username)]:
			db[str(username)] = hash
			db.close()
			return True

	return False

"""                                                                                                
                                                  88                                                                                     88           
                                                  ""                                                                              ,d     88           
                                                                                                                                  88     88           
8b,dPPYba,   ,adPPYba,   ,adPPYb,d8  88       88  88  8b,dPPYba,   ,adPPYba,  ,adPPYba,                ,adPPYYba,  88       88  MM88MMM  88,dPPYba,   
88P'   "Y8  a8P_____88  a8"    `Y88  88       88  88  88P'   "Y8  a8P_____88  I8[    ""                ""     `Y8  88       88    88     88P'    "8a  
88          8PP"""""""  8b       88  88       88  88  88          8PP"""""""   `"Y8ba,                 ,adPPPPP88  88       88    88     88       88  
88          "8b,   ,aa  "8a    ,d88  "8a,   ,a88  88  88          "8b,   ,aa  aa    ]8I                88,    ,88  "8a,   ,a88    88,    88       88  
88           `"Ybbd8"'   `"YbbdP'88   `"YbbdP'Y8  88  88           `"Ybbd8"'  `"YbbdP"'                `"8bbdP"Y8   `"YbbdP'Y8    "Y888  88       88  
                                 88                                                                                                                   
                                 88                                                      888888888888                                                 
"""
def requires_auth(f):
	@wraps(f)
	def decorated(*args, **kwargs):
		auth=request.authorization
		if not auth or not check_auth(auth.username,auth.password):
			return authenticate()
		return f(*args, **kwargs)
	return decorated


"""                                                                                                     
                                  88                                             88                                               
                           ,d     88                                      ,d     ""                            ,d                 
                           88     88                                      88                                   88                 
,adPPYYba,  88       88  MM88MMM  88,dPPYba,    ,adPPYba,  8b,dPPYba,   MM88MMM  88   ,adPPYba,  ,adPPYYba,  MM88MMM   ,adPPYba,  
""     `Y8  88       88    88     88P'    "8a  a8P_____88  88P'   `"8a    88     88  a8"     ""  ""     `Y8    88     a8P_____88  
,adPPPPP88  88       88    88     88       88  8PP"""""""  88       88    88     88  8b          ,adPPPPP88    88     8PP"""""""  
88,    ,88  "8a,   ,a88    88,    88       88  "8b,   ,aa  88       88    88,    88  "8a,   ,aa  88,    ,88    88,    "8b,   ,aa  
`"8bbdP"Y8   `"YbbdP'Y8    "Y888  88       88   `"Ybbd8"'  88       88    "Y888  88   `"Ybbd8"'  `"8bbdP"Y8    "Y888   `"Ybbd8"'                                                                                                                   
"""	
def authenticate():
	return Response('Access DENIED!',401,{'WWW-Authenticate':'Basic realm="Login Required"'})

"""
                                                                           
          d8                         88                                88  
        ,8P'                         88                                88  
       d8"                           88                                88  
     ,8P'     ,adPPYba,   ,adPPYba,  88,dPPYba,    ,adPPYba,   ,adPPYb,88  
    d8"       I8[    ""  a8"     ""  88P'    "8a  a8P_____88  a8"    `Y88  
  ,8P'         `"Y8ba,   8b          88       88  8PP"""""""  8b       88  
 d8"          aa    ]8I  "8a,   ,aa  88       88  "8b,   ,aa  "8a,   ,d88  
8P'           `"YbbdP"'   `"Ybbd8"'  88       88   `"Ybbd8"'   `"8bbdP"Y8  
                                                                           
                                                                           
"""
@app.route('/sched')
@requires_auth
def sched():
    example = SafeConfigParser()
    version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
    configs = {}
    for file in glob(os.path.join("configs_byserial","*.ini")):
        configs[os.path.basename(file)[:-4]] = SafeConfigParser()
        configs[os.path.basename(file)[:-4]].read(file)

    schedules = {}
    for file in glob(os.path.join("schedules","*.p")):
        schedules[os.path.basename(file)[:-12]] = cPickle.load(open(os.path.join("schedules",os.path.basename(file)), 'rb'))
    cfglists = {}
    for file in glob(os.path.join("schedules","*.cfglist")):
        cfglists[os.path.basename(file)[:-18]] = open(file, 'rb').readlines()
        print(os.path.basename(file)[:-18])
    finaldict = {}
    for key, cfglist in cfglists.iteritems():
        oos = {}
        setting = None
        for thing in cfglist:
            if thing.startswith("/"):
                setting = thing.split("/")[-1].strip()
                oos[setting] = {}
            else:
                try:
                    keyval = thing.split(':')
                    if keyval[0] == "Choice":
                        idxval = keyval[1].strip().split(' ')
                        try:
                            oos[setting][keyval[0]].append((idxval[0], ' '.join(idxval[1:]) ))
                        except KeyError:
                            oos[setting][keyval[0]] = []
                            oos[setting][keyval[0]].append((idxval[0], ' '.join(idxval[1:])))
                    else:
                        oos[setting][keyval[0].strip()] = keyval[1].strip()
                except Exception as e:
                    print(str(e))
        otherdict = {}

        for innerkey,value in oos.iteritems():
            if value["Type"] == "RADIO":
                otherdict[innerkey] = copy.deepcopy(value)
        finaldict[key] = copy.deepcopy(otherdict)
    


    return render_template("schedules.html", version=version, configs=configs, schedules=schedules, struct_out_cfg=finaldict)


"""                                                                                                                                                                               
          d8  88                                             d8        a8"                                    88                                                  88           "8a        
        ,8P'  ""                                           ,8P'      a8"                               ,d     88                                           ,d     88             "8a      
       d8"                                                d8"      a8"                                 88     88                                           88     88               "8a    
     ,8P'     88  88,dPYba,,adPYba,    ,adPPYb,d8       ,8P'     a8"        8b,dPPYba,   ,adPPYYba,  MM88MMM  88,dPPYba,   888  8b,dPPYba,   ,adPPYYba,  MM88MMM  88,dPPYba,         "8a  
    d8"       88  88P'   "88"    "8a  a8"    `Y88      d8"       "8a        88P'    "8a  ""     `Y8    88     88P'    "8a  888  88P'    "8a  ""     `Y8    88     88P'    "8a        a8"  
  ,8P'        88  88      88      88  8b       88    ,8P'          "8a      88       d8  ,adPPPPP88    88     88       88       88       d8  ,adPPPPP88    88     88       88      a8"    
 d8"          88  88      88      88  "8a,   ,d88   d8"              "8a    88b,   ,a8"  88,    ,88    88,    88       88  888  88b,   ,a8"  88,    ,88    88,    88       88    a8"      
8P'           88  88      88      88   `"YbbdP"Y8  8P'                 "8a  88`YbbdP"'   `"8bbdP"Y8    "Y888  88       88  888  88`YbbdP"'   `"8bbdP"Y8    "Y888  88       88  a8"        
                                       aa,    ,88                           88                                                  88                                                        
                                        "Y8bbdP"                            88                                                  88                                                        
"""
@app.route("/imgs/<path:path>")
def get_image(path):
	if '..' in path or path.startswith('/'):
		abort(404)
	return send_file(os.path.join("static","temp",path+".jpg"))

def cap_lock_wait(port,serialnumber):
	try:
		a=subprocess.check_output("gphoto2 --port="+str(port)+" --capture-preview --force-overwrite --filename='static/temp/"+str(serialnumber)+".jpg'",shell=True)
		print(a)
		return False
	except subprocess.CalledProcessError as e:
		print(e.output)
		return True

def capture_preview(serialnumber):
	try:
		a = subprocess.check_output("gphoto2 --auto-detect", shell=True)
		for port in re.finditer("usb:", a):
			cmdret = subprocess.check_output('gphoto2 --port "'+a[port.start():port.end()+7]+'" --get-config serialnumber', shell=True)
			_serialnumber = cmdret[cmdret.find("Current: ")+9: len(cmdret)-1]
			port = a[port.start():port.end()+7]
			if _serialnumber == serialnumber:
				tries = 0
				while tries < 10 and cap_lock_wait(port,serialnumber): 
					tries+=1
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
			return send_file("static/temp/"+str(serialnumber)+".jpg")
		else:
			return "fail"
	else:
		return "fail"


@app.route("/sync_hwclock")
@requires_auth
def sync_hwclock():
	print("Synchronising hwclock")
	try:
		cmd = subprocess.check_output("hwclock --systohc",shell=True)
		printcmd
	except Exception as e:
		print("There was a problem Synchronising the hwclock. Debug me please.")
		print("Exception: "+ str(e))
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
	
	config = SafeConfigParser()
	if request.form["name"] == "picam":
		config.read("picam.ini")
	else:
		config.read(os.path.join("configs_byserial",request.form["name"]+'.ini'))
	try:
		subprocess.call("mount /dev/sda1 /mnt/", shell=True)
		shutil.copytree(config.get("localfiles","upload_dir"),os.path.join("/mnt/", config.get("camera","name")))
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
	return "SUCCESS"#'<html><head><script type="text/javascript" //function(){document.location.reload(true);},60000);</script></head><body>UPDATING!! WAIT PLEASE!!</body></html>'

@app.route("/status")
@requires_auth
def status():
	return ''

"""                                                                                          
          d8                                                                                               
        ,8P'                                                                                               
       d8"                                                                                                 
     ,8P'     8b,dPPYba,    ,adPPYba,  8b      db      d8  88       88  ,adPPYba,   ,adPPYba,  8b,dPPYba,  
    d8"       88P'   `"8a  a8P_____88  `8b    d88b    d8'  88       88  I8[    ""  a8P_____88  88P'   "Y8  
  ,8P'        88       88  8PP"""""""   `8b  d8'`8b  d8'   88       88   `"Y8ba,   8PP"""""""  88          
 d8"          88       88  "8b,   ,aa    `8bd8'  `8bd8'    "8a,   ,a88  aa    ]8I  "8b,   ,aa  88          
8P'           88       88   `"Ybbd8"'      YP      YP       `"YbbdP'Y8  `"YbbdP"'   `"Ybbd8"'  88  
"""
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

"""
          d8                       88                      88               
        ,8P'                       88                      ""               
       d8"                         88                                       
     ,8P'     ,adPPYYba,   ,adPPYb,88  88,dPYba,,adPYba,   88  8b,dPPYba,   
    d8"       ""     `Y8  a8"    `Y88  88P'   "88"    "8a  88  88P'   `"8a  
  ,8P'        ,adPPPPP88  8b       88  88      88      88  88  88       88  
 d8"          88,    ,88  "8a,   ,d88  88      88      88  88  88       88  
8P'           `"8bbdP"Y8   `"8bbdP"Y8  88      88      88  88  88       88  
"""
@app.route('/admin')
@requires_auth
def admin():
	version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
	db = anydbm.open('db', 'r')
	usernames = []
	for key,value in db.iteritems():
		usernames.append(key)
	return render_template("admin.html", version=version, usernames=usernames)



@app.route('/botnetmgmt')
@requires_auth
def botnetmgmt():
	# use post later to send commands
	# get hostname:
	jsondata = {}
	version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
	jsondata["version"]=version
	hn = None
	try:
		with open("/etc/hostname","r") as fn:
			hn = fn.readlines()[0]

		a_statvfs = os.statvfs("/")
		free_space = a_statvfs.f_frsize*a_statvfs.f_bavail
		total_space = a_statvfs.f_frsize*a_statvfs.f_blocks
		for x in xrange(0,2):
			free_space /= 1024.0
			total_space /= 1024.0
		jsondata['free_space_mb'] = free_space
		jsondata['total_space_mb'] = total_space
		jsondata["name"]=hn
		rpiconfig = SafeConfigParser()
		rpiconfig.read("picam.ini")
		configs = {}
		for file in glob(os.path.join("configs_byserial","*.ini")):
			configs[os.path.basename(file)[:-4]] = SafeConfigParser()
			configs[os.path.basename(file)[:-4]].read(file)
		jsondata['cameras'] = {}
		for serial, cam_config in configs.iteritems():
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
				ser = str(f.read()).replace("\n", "")
				jsondata['cameras'][ser] = rpc
		except:
			jsondata['cameras']['picam']= rpc
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
				system(" ".join([command,argument]))	
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
		os.remove("/etc/machine-id")
		system("systemd-machine-id-setup")
	except Exception as e:
		resp["ERR"] = str(e)
	return str(json.dumps(resp))


"""
          d8                                    
        ,8P'                             ,d     
       d8"                               88     
     ,8P'     8b,dPPYba,    ,adPPYba,  MM88MMM  
    d8"       88P'   `"8a  a8P_____88    88     
  ,8P'        88       88  8PP"""""""    88     
 d8"          88       88  "8b,   ,aa    88,    
8P'           88       88   `"Ybbd8"'    "Y888                                           
"""
@app.route('/net')
@requires_auth
def network():
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

def commit_ip(ipaddress=None,subnet=None,gateway=None,dev="eth0"):
	if ipaddress is not None and subnet is not None and gateway is not None:
		dev = "eth0"

		broadcast=trunc_at(ipaddress,".")+".255"
		netmask=get_net_size(subnet)
		if not os.path.exists("/etc/conf.d/"):
   			os.makedirs("/etc/conf.d/")
		with open("/etc/conf.d/net-conf-"+dev) as f:
			f.write("address="+ipaddress+"\nnetmask="+netmask+"\nbroadcast="+broadcast+"\ngateway="+gateway)
		with open("/usr/local/bin/net-up.sh") as f:
			script ="""#!/bin/bash
						ip link set dev "$1" up
						ip addr add ${address}/${netmask} broadcast ${broadcast} dev "$1"
						[[ -z ${gateway} ]] || { 
						  ip route add default via ${gateway}
						}
					"""
			f.write(script)
		with open("/usr/local/bin/net-down.sh") as f:
			script ="""#!/bin/bash
						ip addr flush dev "$1"
						ip route flush dev "$1"
						ip link set dev "$1" down
					"""
			f.write(script)
		os.system("chmod +x /usr/local/bin/net-{up,down}.sh")
		with open("/etc/systemd/system/network@.service") as f:
			script ="""[Unit]
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
		os.system("systemctl enable network@"+dev)

def make_dynamic(dev):
	os.system("systemctl disable network@"+dev)
	# do some other crazy stuff here

def set_ip(ipaddress=None,subnet=None,gateway=None,dev="eth0"):
	if ipaddress is not None and subnet is not None and gateway is not None:
		os.system("ip addr add "+ipaddress+"/"+get_net_size(subnet)+" broadcast "+trunc_at(ipaddress,".")+".255 dev "+dev)
		os.system("ip route add default via "+gateway)
	else:
		make_dynamic(dev)



@app.route('/set-ip', methods=['POST'])
@requires_auth
def set_ip():
	if request.method == 'POST':
		try:
			if "ip-form-dynamic" in request.form.keys():
				if request.form['ip-form-dynamic']=="on":
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
def commit_ip():
	if request.method == 'POST':
		try:
			if "ip-form-dynamic" in request.form.keys():
				if request.form['ip-form-dynamic']=="on":
					set_ip()
				else:
					return "fail"
			else:
				try:
					socket.inet_aton(request.form["ip-form-ipaddress"])
					socket.inet_aton(request.form["ip-form-subnet"])
					socket.inet_aton(request.form["ip-form-gateway"])

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
"""                                                        
          d8           88              88                 ad88               
        ,8P'           88              88                d8"                 
       d8"             88              88                88                  
     ,8P'      ,adPPYb,88   ,adPPYba,  88   ,adPPYba,  MM88MMM   ,adPPYb,d8  
    d8"       a8"    `Y88  a8P_____88  88  a8"     ""    88     a8"    `Y88  
  ,8P'        8b       88  8PP"""""""  88  8b            88     8b       88  
 d8"          "8a,   ,d88  "8b,   ,aa  88  "8a,   ,aa    88     "8a,   ,d88  
8P'            `"8bbdP"Y8   `"Ybbd8"'  88   `"Ybbd8"'    88      `"YbbdP"Y8  
                                                                 aa,    ,88  
                                                                  "Y8bbdP"   
"""
@app.route('/delcfg', methods=['POST'])
@requires_auth
def delcfg():
	if request.method =='POST':
		try:
			os.remove(os.path.join("configs_byserial",request.form["name"]+".ini"))
			return "success"
		except:
			return "FAILURE"
"""                                                                                                                            
          d8           88                                                                                                               
        ,8P'           88                ,d                               ,d                                                            
       d8"             88                88                               88                                                            
     ,8P'      ,adPPYb,88   ,adPPYba,  MM88MMM   ,adPPYba,   ,adPPYba,  MM88MMM   ,adPPYba,  ,adPPYYba,  88,dPYba,,adPYba,   ,adPPYba,  
    d8"       a8"    `Y88  a8P_____88    88     a8P_____88  a8"     ""    88     a8"     ""  ""     `Y8  88P'   "88"    "8a  I8[    ""  
  ,8P'        8b       88  8PP"""""""    88     8PP"""""""  8b            88     8b          ,adPPPPP88  88      88      88   `"Y8ba,   
 d8"          "8a,   ,d88  "8b,   ,aa    88,    "8b,   ,aa  "8a,   ,aa    88,    "8a,   ,aa  88,    ,88  88      88      88  aa    ]8I  
8P'            `"8bbdP"Y8   `"Ybbd8"'    "Y888   `"Ybbd8"'   `"Ybbd8"'    "Y888   `"Ybbd8"'  `"8bbdP"Y8  88      88      88  `"YbbdP"'  
                                                                                                                                        
                                                                                                                                        
"""
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
"""                                                                            
          d8                                  88                                      ad88               
        ,8P'                                  ""    ,d                               d8"                 
       d8"                                          88                               88                  
     ,8P'     8b      db      d8  8b,dPPYba,  88  MM88MMM   ,adPPYba,   ,adPPYba,  MM88MMM   ,adPPYb,d8  
    d8"       `8b    d88b    d8'  88P'   "Y8  88    88     a8P_____88  a8"     ""    88     a8"    `Y88  
  ,8P'         `8b  d8'`8b  d8'   88          88    88     8PP"""""""  8b            88     8b       88  
 d8"            `8bd8'  `8bd8'    88          88    88,    "8b,   ,aa  "8a,   ,aa    88     "8a,   ,d88  
8P'               YP      YP      88          88    "Y888   `"Ybbd8"'   `"Ybbd8"'    88      `"YbbdP"Y8  
                                                                                             aa,    ,88  
                                                                                              "Y8bbdP"   
"""
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
			#print"key:" + key +"  value:"+value
			if value != "" and key != "config-name":
				sect = key.split('.')[0]
				opt = key.split(".")[1]
				aconfig.set(sect,opt,value)
				print("changed: "+sect+':'+opt+':'+value)
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

			config = SafeConfigParser()
			config_path = "eyepi.ini"
			config.read(config_path)
			config.set("camera","name",hostname)
			pi_config = SafeConfigParser()
			pi_config_path = "picam.ini"
			pi_config.read(config_path)
			pi_config.set("camera","name",hostname+"-Picam")
			hostsfilestring = """#
# /etc/hosts: static lookup table for host names
#

#<ip-address>	<hostname.domain.org>	<hostname>
127.0.0.1	localhost.localdomain	localhost CHANGE
::1		localhost.localdomain	localhost CHANGE

# End of file
"""
			try:
				with open("/etc/hosts",'w') as hostsfile:
					hostsfile.write(hostsfilestring.replace("CHANGE",hostname))

				with open("/etc/hostname",'w') as hostnamefile:
					hostnamefile.write(hostname+'\n')
				os.system("hostname "+hostname)
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
"""    
          d8  
        ,8P'  
       d8"    
     ,8P'     
    d8"       
  ,8P'        
 d8"          
8P'           
              
              
"""
@app.route('/')
@requires_auth
def config():
	example = SafeConfigParser()
	version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
	rpiconfig = SafeConfigParser()
	rpiconfig.read("picam.ini")
	example.read("example.ini")
	configs = {}
	for file in glob(os.path.join("configs_byserial","*.ini")):
		configs[os.path.basename(file)[:-4]] = SafeConfigParser()
		configs[os.path.basename(file)[:-4]].read(file)
	return render_template("config.html", version=version, configs = configs, rpiconfig = rpiconfig, example=example)
"""
                                                                                                                                                                                   
          d8     ad88  88  88                                                                                                                                                      
        ,8P'    d8"    ""  88                                                                                                                                               ,d     
       d8"      88         88                                                                                                                                               88     
     ,8P'     MM88MMM  88  88   ,adPPYba,  88,dPYba,,adPYba,   ,adPPYYba,  8b,dPPYba,   ,adPPYYba,   ,adPPYb,d8   ,adPPYba,  88,dPYba,,adPYba,    ,adPPYba,  8b,dPPYba,   MM88MMM  
    d8"         88     88  88  a8P_____88  88P'   "88"    "8a  ""     `Y8  88P'   `"8a  ""     `Y8  a8"    `Y88  a8P_____88  88P'   "88"    "8a  a8P_____88  88P'   `"8a    88     
  ,8P'          88     88  88  8PP"""""""  88      88      88  ,adPPPPP88  88       88  ,adPPPPP88  8b       88  8PP"""""""  88      88      88  8PP"""""""  88       88    88     
 d8"            88     88  88  "8b,   ,aa  88      88      88  88,    ,88  88       88  88,    ,88  "8a,   ,d88  "8b,   ,aa  88      88      88  "8b,   ,aa  88       88    88,    
8P'             88     88  88   `"Ybbd8"'  88      88      88  `"8bbdP"Y8  88       88  `"8bbdP"Y8   `"YbbdP"Y8   `"Ybbd8"'  88      88      88   `"Ybbd8"'  88       88    "Y888  
                                                                                                     aa,    ,88                                                                    
                                                                                                      "Y8bbdP"                                                                     
"""
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

"""                                                   
          d8     ad88  88  88              88  88                      
        ,8P'    d8"    ""  88              88  ""               ,d     
       d8"      88         88              88                   88     
     ,8P'     MM88MMM  88  88   ,adPPYba,  88  88  ,adPPYba,  MM88MMM  
    d8"         88     88  88  a8P_____88  88  88  I8[    ""    88     
  ,8P'          88     88  88  8PP"""""""  88  88   `"Y8ba,     88     
 d8"            88     88  88  "8b,   ,aa  88  88  aa    ]8I    88,    
8P'             88     88  88   `"Ybbd8"'  88  88  `"YbbdP"'    "Y888  
                                                                       
                                                                       
"""
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
		
"""                                                                               
          d8  88                                                                      
        ,8P'  ""                                                                      
       d8"                                                                            
     ,8P'     88  88,dPYba,,adPYba,   ,adPPYYba,   ,adPPYb,d8   ,adPPYba,  ,adPPYba,  
    d8"       88  88P'   "88"    "8a  ""     `Y8  a8"    `Y88  a8P_____88  I8[    ""  
  ,8P'        88  88      88      88  ,adPPPPP88  8b       88  8PP"""""""   `"Y8ba,   
 d8"          88  88      88      88  88,    ,88  "8a,   ,d88  "8b,   ,aa  aa    ]8I  
8P'           88  88      88      88  `"8bbdP"Y8   `"YbbdP"Y8   `"Ybbd8"'  `"YbbdP"'  
                                                   aa,    ,88                         
                                                    "Y8bbdP"                          
"""
@app.route("/images")
def images():
	version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
	example = SafeConfigParser()
	version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
	example.read("example.ini")

	configs = {}
	rpiconfig = SafeConfigParser()
	rpiconfig.read("picam.ini")
	for file in glob(os.path.join("configs_byserial","*.ini")):
		configs[os.path.basename(file)[:-4]] = SafeConfigParser()
		configs[os.path.basename(file)[:-4]].read(file)
	urls = []
	for file in glob(os.path.join("static","temp","*.jpg")):
		urls.append(os.path.basename(file)[:-4])
	return render_template("images.html", version=version, configs=configs, rpiconfig=rpiconfig, image_urls=urls,example=example)
"""                                                                                                                                             
          d8                                       ad88  88  88                                                        88  88                            
        ,8P'                             ,d       d8"    ""  88    ,d                                                  88  88                            
       d8"                               88       88         88    88                                                  88  88                            
     ,8P'      ,adPPYb,d8   ,adPPYba,  MM88MMM  MM88MMM  88  88  MM88MMM   ,adPPYba,  8b,dPPYba,   ,adPPYba,   ,adPPYb,88  88   ,adPPYba,    ,adPPYb,d8  
    d8"       a8"    `Y88  a8P_____88    88       88     88  88    88     a8P_____88  88P'   "Y8  a8P_____88  a8"    `Y88  88  a8"     "8a  a8"    `Y88  
  ,8P'        8b       88  8PP"""""""    88       88     88  88    88     8PP"""""""  88          8PP"""""""  8b       88  88  8b       d8  8b       88  
 d8"          "8a,   ,d88  "8b,   ,aa    88,      88     88  88    88,    "8b,   ,aa  88          "8b,   ,aa  "8a,   ,d88  88  "8a,   ,a8"  "8a,   ,d88  
8P'            `"YbbdP"Y8   `"Ybbd8"'    "Y888    88     88  88    "Y888   `"Ybbd8"'  88           `"Ybbd8"'   `"8bbdP"Y8  88   `"YbbdP"'    `"YbbdP"Y8  
               aa,    ,88                                                                                                                    aa,    ,88  
                "Y8bbdP"                                                                                                                      "Y8bbdP"   
"""
@app.route("/getfilteredlog", methods=["POST"])
@requires_auth
def getfilteredlog():
	if request.method == 'POST':
		query = request.form["query"].lower()
		returnstring = ''
		with open("spc-eyepi.log",'rb') as f:
			f.seek (0, 2)
			fsize = f.tell()
			f.seek (max (fsize-10.24**6, 0), 0)
			lines = f.readlines() 
			a = reversed(lines)
		for line in a:
			if fnmatch.fnmatch(line.lower(),"*"+query.lower()+"*") and len(returnstring.splitlines())<250:
				returnstring += "<tr><td>"+line+"</td></tr>"+'\n'
		returnstring+="<tr><td><h3>Truncated at 250 lines of 1Mb into the past</h3></td></tr>"
		return returnstring
	else:
		abort(400)
"""                                                                    
          d8  88                                 88                            
        ,8P'  88                                 88                            
       d8"    88                                 88                            
     ,8P'     88   ,adPPYba,    ,adPPYb,d8       88   ,adPPYba,    ,adPPYb,d8  
    d8"       88  a8"     "8a  a8"    `Y88       88  a8"     "8a  a8"    `Y88  
  ,8P'        88  8b       d8  8b       88       88  8b       d8  8b       88  
 d8"          88  "8a,   ,a8"  "8a,   ,d88  888  88  "8a,   ,a8"  "8a,   ,d88  
8P'           88   `"YbbdP"'    `"YbbdP"Y8  888  88   `"YbbdP"'    `"YbbdP"Y8  
                                aa,    ,88                         aa,    ,88  
                                 "Y8bbdP"                           "Y8bbdP"   
"""
@app.route("/log.log")
@requires_auth
def log():
	return send_file("spc-eyepi.log")
"""                                                                                                       
          d8           88              88                                      ad88  88  88                         
        ,8P'           88              88                ,d                   d8"    ""  88                         
       d8"             88              88                88                   88         88                         
     ,8P'      ,adPPYb,88   ,adPPYba,  88   ,adPPYba,  MM88MMM   ,adPPYba,  MM88MMM  88  88   ,adPPYba,  ,adPPYba,  
    d8"       a8"    `Y88  a8P_____88  88  a8P_____88    88     a8P_____88    88     88  88  a8P_____88  I8[    ""  
  ,8P'        8b       88  8PP"""""""  88  8PP"""""""    88     8PP"""""""    88     88  88  8PP"""""""   `"Y8ba,   
 d8"          "8a,   ,d88  "8b,   ,aa  88  "8b,   ,aa    88,    "8b,   ,aa    88     88  88  "8b,   ,aa  aa    ]8I  
8P'            `"8bbdP"Y8   `"Ybbd8"'  88   `"Ybbd8"'    "Y888   `"Ybbd8"'    88     88  88   `"Ybbd8"'  `"YbbdP"'  
                                                                                                                    
                                                                                                                    
"""
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
"""                                                            
          d8  88                               ad88  88  88              
        ,8P'  88                              d8"    ""  88              
       d8"    88                              88         88              
     ,8P'     88   ,adPPYba,    ,adPPYb,d8  MM88MMM  88  88   ,adPPYba,  
    d8"       88  a8"     "8a  a8"    `Y88    88     88  88  a8P_____88  
  ,8P'        88  8b       d8  8b       88    88     88  88  8PP"""""""  
 d8"          88  "8a,   ,a8"  "8a,   ,d88    88     88  88  "8b,   ,aa  
8P'           88   `"YbbdP"'    `"YbbdP"Y8    88     88  88   `"Ybbd8"'  
                                aa,    ,88                               
                                 "Y8bbdP"                                
"""
@app.route("/logfile")
@requires_auth
def logfile():
	version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
	return render_template("logpage.html", version=version)

@app.route("/<any('css','js'):selector>/<path:path>")
@requires_auth
def get_resource(selector, path):
	return send_from_directory("static",filename=os.path.join(selector,path))

"""                                           
                                                            88                                           
                                                            ""                                           
                                                                                                         
                            88,dPYba,,adPYba,   ,adPPYYba,  88  8b,dPPYba,                               
                            88P'   "88"    "8a  ""     `Y8  88  88P'   `"8a                              
                            88      88      88  ,adPPPPP88  88  88       88                              
                            88      88      88  88,    ,88  88  88       88                              
                            88      88      88  `"8bbdP"Y8  88  88       88                              
                                                                                                         
888888888888  888888888888                                                   888888888888  888888888888  
"""
if __name__ == "__main__":
	app.run(host='0.0.0.0')

