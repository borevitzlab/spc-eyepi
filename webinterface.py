#!/usr/bin/env python
import socket, os, hashlib, subprocess
import Crypto.Protocol.KDF
import anydbm
import datetime, re, fnmatch, shutil
import cPickle
import copy
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
	print "do checking here"
	with open(filename, 'wb') as configfile:
		towriteconfig.write(configfile)


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
		print str(e)

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
        print os.path.basename(file)[:-18]
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
                    print str(e)
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
		print str(e)
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
	print "shutting down"
	os.system("reboot")
	return redirect(url_for('admin'))

@app.route("/update")
@requires_auth
def update():
	os.system("git fetch --all")
	os.system("git reset --hard origin/master")
	return redirect(url_for('admin'))#'<html><head><script type="text/javascript" //function(){document.location.reload(true);},60000);</script></head><body>UPDATING!! WAIT PLEASE!!</body></html>'

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
	#	abort(400)

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
	try:
		if os.path.isfile("interfaces"):
			netcfg = open("interfaces",'r')
		else:
			os.symlink("/etc/network/interfaces")
			netcfg = open("interfaces",'r')
	except:
		abort(500)
	return render_template("network.html", version=version, netcfg = netcfg)
"""

"""
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
		
		fsize = os.stat("spc-eyepi.log")
		bufsize = 8192
		def tail( f, lines=20 ):
			total_lines_wanted = lines

			BLOCK_SIZE = 1024
			f.seek(0, 2)
			block_end_byte = f.tell()
			lines_to_go = total_lines_wanted
			block_number = -1
			blocks = [] # blocks of size BLOCK_SIZE, in reverse order starting
						# from the end of the file
			while lines_to_go > 0 and block_end_byte > 0:
				if (block_end_byte - BLOCK_SIZE > 0):
					# read the last block we haven't yet read
					f.seek(block_number*BLOCK_SIZE, 2)
					blocks.append(f.read(BLOCK_SIZE))
				else:
					# file too small, start from begining
					f.seek(0,0)
					# only read what was not read
					blocks.append(f.read(block_end_byte))
				lines_found = blocks[-1].count('\n')
				lines_to_go -= lines_found
				block_end_byte -= BLOCK_SIZE
				block_number -= 1
			all_read_text = ''.join(blocks)
			return '\n'.join(all_read_text.splitlines()[-total_lines_wanted:])

		with open("spc-eyepi.log") as f:
			a = tail(f,2000)
		for line in a.splitlines():
			if fnmatch.fnmatch(line.lower(),"*"+query.lower()+"*") and len(returnstring.splitlines())<100:
					returnstring += "<tr><td>"+line+"</td></tr>"+'\n'
		returnstring+="<tr><td><h3>Truncated at 100/2000 lines into the past</h3></td></tr>"

		"""
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
			return returnstring"""
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

