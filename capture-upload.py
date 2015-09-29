#!/usr/bin/python2
from __future__ import division
import os, subprocess, sys, platform, io, json
import datetime, time, shutil, re
import pysftp, ftplib
import logging, logging.config
import cPickle
from glob import glob
from ConfigParser import SafeConfigParser
from optparse import OptionParser
from threading import Thread
from socket import socket, SOCK_DGRAM, AF_INET
import pyudev

# Global configuration variables
timestartfrom = datetime.time.min
timestopat = datetime.time.max
default_extension = ".JPG"
#Acceptable filtypes
filetypes = ["CR2","RAW","NEF","JPG","JPEG"]
logging.config.fileConfig("logging.ini")
logging.getLogger("paramiko").setLevel(logging.WARNING)

def timestamp(tn):
    """ Build a timestamp in the required format
    """
    st = tn.strftime('%Y_%m_%d_%H_%M_%S')
    return st

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

def geteosserialnumber(port):
    try:
        cmdret = subprocess.check_output('gphoto2 --port "'+port+'" --get-config eosserialnumber', shell=True)
        return cmdret[cmdret.find("Current: ")+9: len(cmdret)-1]
    except:

        return 0

class Camera(Thread):
    """ Big Camera Class,
        PiCamera extends this class
    """
    def __init__(self, config_filename, name = None, serialnumber = None, camera_port = None):
        # init with name or not, just extending some of the functionality of Thread 
        if name == None:
            Thread.__init__(self)
        else:
            Thread.__init__(self, name=name)
        
        if serialnumber != None:
            self.serialnumber = serialnumber
        else: 
            self.serialnumber = "[no-cam-sn-detected] wtf?"
        if camera_port!=None:
            self.camera_port = camera_port
        else:
            self.camera_port = "[no-camera-port-detected] wtf?"
        # variable setting and config file jiggery.
        self.last_config_modify_time = None
        self.config_filename = config_filename
        if not os.path.isfile(self.config_filename):
            eosserial = geteosserialnumber(self.camera_port) 
            create_config(name, eosserial = eosserial)
            self.config_filename = os.path.join("configs_byserial",serialnumber+".ini")
        self.logger = logging.getLogger(self.getName())
        # run setup(), there is a separate setup() function so that it can be called again in the event of settings changing
        self.setup()
        
    def setup(self):
        # setup new config parser and parse config
        self.config = SafeConfigParser()
        self.config.read(self.config_filename)
        # accuracy is really the timeout before it gives up and waits for the next time period
        self.accuracy = 3
        # get details from config file
        self.cameraname = self.config.get("camera","name")
        self.interval = self.config.getint("timelapse","interval")
        self.spool_directory = self.config.get("localfiles","spooling_dir")
        self.upload_directory = self.config.get("localfiles","upload_dir")
        #self.exposure_length = self.config.getint("camera","exposure")
        self.last_config_modify_time = os.stat(self.config_filename).st_mtime
        # get enabled
        if self.config.get("camera","enabled")=="on":
            self.is_enabled= True
        else:
            self.is_enabled = False
        try:
            tval = self.config.get('timelapse','starttime')
            if len(tval)==5:
                if tval[2]==':':
                    self.timestartfrom = datetime.time(int(tval[:2]),int(tval[3:]))
                    self.logger.info("Starting at %s" % self.timestartfrom.isoformat())
        except Exception, e:
            self.logger.error("Time conversion error startime - %s" % str(e))
        try:
            tval = self.config.get('timelapse','stoptime')
            if len(tval)==5:
                if tval[2]==':':
                    self.timestopat = datetime.time(int(tval[:2]),int(tval[3:]))
                    self.logger.info("Stopping at %s" % self.timestopat.isoformat())
        except Exception, e:
            self.logger.error("Time conversion error stoptime - %s" % str(e))

        # create spooling and upload directories if they dont exist, and delete files in the spooling dir
        if not os.path.exists(self.spool_directory):
            # All images stored in their own seperate directory
            self.logger.info("Creating Image Storage directory %s" % self.spool_directory)
            os.makedirs(self.spool_directory)
        else:
            for the_file in os.listdir(self.spool_directory):
                file_path =os.path.join(self.spool_directory, the_file)
                try:
                    if os.path.isfile(file_path):
                        os.unlink(file_path)
                        self.logger.info("Deleting previous file in the spool eh, Sorry.")
                except Exception, e:
                    self.logger.error("Sorry, buddy! Couldn't delete the files in spool, eh! Error: %s" % e)
        if not os.path.exists(self.upload_directory):
            self.logger.info("creating copyfrom dir %s" % self.upload_directory)
            os.makedirs(self.upload_directory)
            
    def timestamped_imagename(self,timen):
        """ Build the pathname for a captured image.
            TODO: Remove the need for a default extension!
            Its useless in our extension agnostic capture.
        """
        return os.path.join(self.cameraname + '_' + timestamp(timen) + default_extension)

    def time2seconds(self, t):
        """ Convert the time to seconds
            TODO: a better implementation of this such as datetime.timesinceepoch or some sorcery
        """
        return t.hour*60*60+t.minute*60+t.second

    def capture(self, raw_image,try_number):
        cmd = ["gphoto2 --port "+self.camera_port+" --set-config capturetarget=sdram --capture-image-and-download --filename='"+os.path.join(self.spool_directory, os.path.splitext(raw_image)[0])+".%C'"]
        try:
            output = subprocess.check_output(cmd,stderr=subprocess.STDOUT,universal_newlines=True,shell=True)
            for line in output.splitlines():
                self.logger.info("GPHOTO2: "+ line)
            return True
        except Exception as e:
            if try_number>2:
                for line in e.output.splitlines():
                    if not line.strip() == "" and not "***" in line:
                        self.logger.error(line.strip())
            return False

    def run(self):
        # set the next capture time to now just because
        self.next_capture = datetime.datetime.now()
        # this is to test and see if the config has been modified
        
        while (True):
            # testing for the config modification
            if os.stat(self.config_filename).st_mtime!=self.last_config_modify_time:
                self.last_config_modify_time = os.stat(self.config_filename).st_mtime
                # Resetup()
                self.setup()
                self.logger.info("Change in config file at "+ datetime.datetime.now().isoformat() +" reloading")
            
            # set a timenow, this is used everywhere ahead, do not remove.
            tn = datetime.datetime.now()

            # checking if enabled and other stuff
            if (self.time2seconds(tn)%self.interval< self.accuracy) and (tn.time() > self.timestartfrom) and (tn.time() < self.timestopat) and (self.is_enabled):
                try:
                    # set the next capture period to print to the log (not used anymore, really due to time modulo) 
                    self.next_capture = tn + datetime.timedelta(seconds = self.interval)
                    # The time now is within the operating times
                    self.logger.info("Capturing Image now for %s" % self.serialnumber)
                    
                    # setting variables for saving files
                    # TODO:
                    # 1. Here is where the raw_image should be just timestamped imagename minus extension so that things are more extension agnostic
                    raw_image = self.timestamped_imagename(tn)
                    jpeg_image = self.timestamped_imagename(tn)[:-4]+".jpg"
                    
                    
                    # TODO:
                    # 3. put other camera settings in another call to setup camera (iso, aperture etc) using gphoto2 --set-config (nearly done)
                    
                    # No conversion needed, just take 2 files, 1 jpeg and 1 raw
                    #if self.camera_port:
                    # stuff for checking bulb. not active yet
                    #is_bulbspeed = subprocess.check_output("gphoto2 --port "+self.camera_port+" --get-config shutterspeed", shell=True).splitlines() 
                    #bulb = is_bulbspeed[3][is_bulbspeed[3].find("Current: ")+9: len(is_bulbspeed[3])]
                    #if bulb.find("bulb") != -1:
                    #    cmd = ["gphoto2 --port "+ self.camera_port+" --set-config capturetarget=sdram --set-config eosremoterelease=5 --wait-event="+str(self.exposure_length)+"ms --set-config eosremoterelease=11 --wait-event-and-download=2s --filename='"+os.path.join(self.spool_directory, os.path.splitext(raw_image)[0])+".%C'"]
                    #else:
                    #cmd = ["gphoto2 --port "+self.camera_port+" --set-config capturetarget=sdram --capture-image-and-download --wait-event-and-download=36s --filename='"+os.path.join(self.spool_directory, os.path.splitext(raw_image)[0])+".%C'"]
                    
                    try:
                        try_number = 0
                        while not self.capture(raw_image,try_number):
                            try_number+=1
                            time.sleep(1)
                        self.logger.debug("Capture Complete")
                        self.logger.debug("Moving and renaming image files, buddy")
                    except Exception as e:
                        self.logger.error("Something went really wrong!")
                        self.logger.error(str(e))
                        time.sleep(5)

                    # glob together all filetypes in filetypes array
                    files = []
                    for filetype in filetypes:
                        files.extend(glob(os.path.join(self.spool_directory,"*."+filetype.upper())))
                        files.extend(glob(os.path.join(self.spool_directory,"*."+filetype.lower())))
                        
                    # copying/renaming for files
                    for file in files:
                        # get the extension and basename
                        ext = os.path.splitext(file)[-1].lower()
                        name = os.path.splitext(raw_image)[0]
                        # copy jpegs to the static web dir, and to the upload dir (if upload webcam flag is set)
                        if ext == ".jpeg" or ".jpg":
                            # best to create a symlink to /dev/shm/ from static/temp
                            # TODO: multicamera support will need changes here!!
                            shutil.copy(file,os.path.join("/dev/shm", self.serialnumber+".jpg"))
                            if self.config.get("ftp","uploadwebcam") == "on":
                                shutil.copy(file,os.path.join(self.upload_directory, "dslr_last_image.jpg"))
                        # move timestamped image te be uploaded
                        if self.config.get("ftp","uploadtimestamped")=="on":
                            self.logger.debug("saving timestamped image for you, buddy")
                            os.rename(file, os.path.join(self.upload_directory, os.path.basename(name+ext)))
                        else:
                            self.logger.debug("deleting file, eh")
                            os.remove(file)
                        self.logger.info("Captured and stored - %s" % os.path.basename(name+ext))
                    # Log Delay/next shots
                    if self.next_capture.time() < self.timestopat:
                        self.logger.info("Next capture at %s" % self.next_capture.isoformat())
                    else:
                        self.logger.info("Capture will stop at %s" % self.timestopat.isoformat())
                except Exception, e:
                    self.next_capture = datetime.datetime.now()
                    # TODO: This needs to catch errors from subprocess.call because it doesn't
                    self.logger.error("Image Capture error - " + str(e))
            time.sleep(0.1)

class PiCamera(Camera):
    """ PiCamera extension to the Camera Class
        extends some functionality and members, modified image capture call and placements.
    """
    def run(self):
        # set next_capture, this isnt really used much anymore except for logging.
        self.next_capture = datetime.datetime.now()
        # this is to test and see if the config has been modified
        while (True):
            # set a timenow this is used locally down here
            tn = datetime.datetime.now()

            # testing for the config modification
            if os.stat(self.config_filename).st_mtime!=self.last_config_modify_time:
                self.last_config_modify_time = os.stat(self.config_filename).st_mtime
                # Resetup()
                self.setup()
                self.logger.info("change in config at "+ datetime.datetime.now().isoformat() +" reloading")
            
            if (self.time2seconds(tn)%(86400/24) < self.accuracy):
                files = []
                # once per hour
                # remove weird images that appear in the working dir. 
                # TODO: fix this so its not so hacky, need to find out why the 
                # picam is leaving jpegs in the working directoy.
                for filetype in filetypes:
                    files.extend(glob("/home/spc-eyepi/*."+filetype.upper()+"\~"))
                    files.extend(glob("/home/spc-eyepi/*."+filetype.lower()+"\~"))
                    files.extend(glob("/home/spc-eyepi/*."+filetype.upper()))
                    files.extend(glob("/home/spc-eyepi/*."+filetype.lower()))
                for fn in files:
                    os.remove(fn)
            
            if (self.time2seconds(tn)%self.interval< self.accuracy) and (tn.time() > self.timestartfrom) and (tn.time() < self.timestopat) and (self.is_enabled):
                try:
                    # change the next_capture for logging. not really used much anymore.
                    self.next_capture = tn + datetime.timedelta(seconds = self.interval)
                        
                    # The time now is within the operating times
                    self.logger.info("Capturing Image now for picam")
                    # TODO: once timestamped imagename is more agnostic this will require a jpeg append.
                    image_file = self.timestamped_imagename(tn)

                    image_file_path = os.path.join(self.spool_directory, image_file)
                    # take the image using os.system(), pretty hacky but it cant exactly be run on windows.
                    if self.config.has_section("picam_size"):
                        os.system("/opt/vc/bin/raspistill -w "+self.config.get("picam_size","width")+" -h "+self.config.get("picam_size","height")+" --nopreview -o " + image_file)    
                    else:
                        os.system("/opt/vc/bin/raspistill --nopreview -o " + image_file)
                    os.chmod(image_file,0755)
                    self.logger.debug("Capture Complete")
                    self.logger.debug("Copying the image to the web service, buddy")
                    # Copy the image file to the static webdir 
                    shutil.copy(image_file,os.path.join("static","temp","pi_last_image.jpg"))
                    # webcam copying
                    if self.config.get("ftp","uploadwebcam") == "on":
                        shutil.copy(image_file,os.path.join(self.upload_directory, "pi_last_image.jpg"))
                    # rename for timestamped upload
                    if self.config.get("ftp","uploadtimestamped")=="on":
                        self.logger.debug("saving timestamped image for you, buddy")
                        os.rename(image_file ,os.path.join(self.upload_directory,os.path.basename(image_file))) 
                    else:
                        self.logger.debug("deleting file buddy")
                        os.remove(image_file)
                    # Do some logging.
                    if self.next_capture.time() < self.timestopat:
                        self.logger.info("Next capture at %s" % self.next_capture.isoformat())
                    else:
                        self.logger.info("Capture will stop at %s" % self.timestopat.isoformat())
                        
                except Exception, e:
                    self.next_capture = datetime.datetime.now()
                    self.logger.error("Image Capture error - " + str(e))

            time.sleep(0.1)
        

class Uploader(Thread):
    """ Uploader class,
        used to upload,
    """
    def __init__(self, config_filename, name = None):
        # same thread name hackery that the Camera threads use
        if name == None:
            Thread.__init__(self)
        else:
            Thread.__init__(self, name=name)

        # and the same setup stuff that they use as well.
        self.last_config_modify_time = None
        self.config_filename = config_filename
        self.logger = logging.getLogger(self.getName())
        self.startup_time = datetime.datetime.now()
        self.total_data_uploaded_tb = 0
        self.total_data_uploaded_b = 0
        self.setup()

    def setup(self):
        # TODO: move the timeinterval to the config file and get it from there, this _should_ avoid too many requests to the sftp server.
        self.timeinterval = 30
        self.config = SafeConfigParser()
        self.config.read(self.config_filename)
        self.hostname = self.config.get("ftp","server")
        self.user = self.config.get("ftp","user")
        self.passwd = self.config.get("ftp","pass")
        self.target_directory = self.config.get("ftp", "directory")
        self.cameraname = self.config.get("camera","name")
        self.upload_directory = self.config.get("localfiles","upload_dir")
        # these things are to none now so we can check for None later.
        self.last_upload_time = None
        self.ipaddress = None

    def sendMetadataSFTP(self,datas):
        """ Stores IP address on server using SecureFTP
            *datas is a dictionary of metadata files as datas[fname]=data
        """
        try:
            #self.logger.debug("trying to store new ip on server using SFTP, friend!")
            # create new link and create directory if it doesnt exist already
            link = pysftp.Connection(host=self.hostname, username=self.user, password=self.passwd)
            link.chdir("/")
            self.mkdir_p_sftp(link, os.path.join(self.target_directory,self.cameraname) )
            # open a file and write the html snippet.
            for name,data in datas.iteritems():
                f = link.open(os.path.join(self.target_directory,self.cameraname,name), mode='w')
                f.write(data)
                f.close()
        except Exception as e:
            # this is going to trigger if the user provided cannot log into SFTP (ie they give an ftp user/pass
            self.logger.warning("SFTP:  "+ str(e))
            return False
        return True    

    def sendMetadataFTP(self,datas):
        """ Stores IP address on server using FTP
            *datas is a dictionary of metadata files as datas[fname]=data
        """
        try:
            #self.logger.debug("trying to store metadata on server using FTP, friend!")
            # similar to the SFTP
            ftp = ftplib.FTP(self.hostname)
            ftp.login(self.user,self.passwd)
            self.mkdir_p_ftp(ftp, os.path.join(self.target_directory,self.cameraname))
            for name,data in datas.iteritems():
                # some sorcery that I dont understand:
                unicodestring = unicode(data)
                assert isinstance(unicodestring, unicode)
                # I think this makes it into a file-like object?
                file = io.BytesIO(unicodestring.encode("utf-8"))
                # upload it
                ftp.storbinary('STOR '+name,file)
            ftp.quit()
        except Exception as e:
            # if FTP fails log an error not a warning like SFTP
            self.logger.error(str(e))
            return False
        return True

    def sftpUpload(self,filenames):
        """ Secure upload the image file to the Server
        """
        try:
            self.logger.debug("Connecting sftp and uploading buddy")
            # open link and create directory if for some reason it doesnt exist
            link = pysftp.Connection(host=self.hostname, username=self.user, password=self.passwd)
            link.chdir("/")
            self.mkdir_p_sftp(link, os.path.join(self.target_directory,self.cameraname))
            self.logger.debug("Uploading")
            # dump ze files.
            for f in filenames:
                # use sftpuloadtracker to handle the progress
                link.put(f,os.path.basename(f)+".tmp", callback=self.sftpuploadtracker)
                if link.exists(os.path.basename(f)):
                    link.remove(os.path.basename(f))
                link.rename(os.path.basename(f)+".tmp",os.path.basename(f))

                link.chmod(os.path.basename(f), mode=775)
                self.total_data_uploaded_b += os.path.getsize(f)
                os.remove(f)
                self.logger.debug("Successfuly uploaded %s through sftp and removed from local filesystem" % f)
            self.logger.debug("Disconnecting, eh")
            link.close()
            if self.total_data_uploaded_b > 1000000000000:
                curr = (((self.total_data_uploaded_b/1024)/1024)/1024)/1024
                self.total_data_uploaded_b = 0
                self.total_data_uploaded_tb = curr

        except Exception as e:
            # log a warning if fail because SFTP is meant to fail to allow FTP fallback
            self.logger.warning("SFTP:  " + str(e))
            return False
        return True
      

    def ftpUpload(self, filenames):
        """ insecure upload for backwards compatibility
        """
        self.logger.info("Looks like I can't make a connection using sftp, eh. Falling back to ftp.")
        try:
            self.logger.debug("Connecting ftp")
            # open link and create directory if for some reason it doesnt exist 
            ftp = ftplib.FTP(self.hostname)
            ftp.login(self.user,self.passwd)
            self.mkdir_p_ftp(ftp, os.path.join(self.target_directory,self.cameraname))
            self.logger.info("Uploading")
            # dump ze files.
            for f in filenames:
                totalSize = os.path.getsize(f)
                # use ftpuploadtracker class to handle the progress
                uploadTracker = FtpUploadTracker(totalSize)
                ftp.storbinary('stor '+ os.path.basename(f), open(f, 'rb'), 1024, uploadTracker.handle)
                self.logger.debug("Successfuly uploaded %s through ftp and removed from local filesystem" % f)
                os.remove(f)
        except Exception as e:
            # log error if cant upload using FTP. FTP is last resort.
            self.logger.error(str(e))
            return False
        return True

    def mkdir_p_sftp(self, sftp, remote_directory):
        """ Recursive directory sorcery for SecureFTP
        """
        if remote_directory == '/':
            sftp.chdir('/')
            return
        if remote_directory =='':
            return
        remote_dirname, basename = os.path.split(remote_directory)
        self.mkdir_p_sftp(sftp, os.path.dirname(remote_directory))
        try:
            sftp.chdir(basename)
        except IOError:
            self.logger.info("Sorry, just have to make some new directories, eh. (sftp)")
            sftp.mkdir(basename)
            sftp.chdir(basename)

    def mkdir_p_ftp(self, ftp, remote_directory):
        """ Recursive directory sorcery for FTP
        """
        if remote_directory == '/':
            ftp.cwd('/')
            return
        if remote_directory =='':
            return
        remote_dirname, basename = os.path.split(remote_directory)
        self.mkdir_p_ftp(ftp, os.path.dirname(remote_directory))
        try:
            ftp.cwd(basename)
        except ftplib.error_perm as e:
            self.logger.info("Sorry, just have to make some new directories, eh. (ftp)")
            ftp.mkd(basename)
            ftp.cwd(basename)

    def sftpuploadtracker(self,transferred, total):
        """ Outputs status on sftpupload
        """
        if total/100 != 0:
            if (transferred % (total/100)):
                percentage = round((transferred / total)*100)
                sys.stderr.write('\r[{0}] {1}%'.format('.'*int(percentage),int(percentage)))
                sys.stderr.flush()

            
    def set_metadata_on_server(self, list_of_uploads):
        """ Metadata collector
        """
        def sizeof_fmt(num, suffix='B'):
            for unit in ['','Ki','Mi','Gi','Ti','Pi','Ei','Zi']:
                if abs(num) < 1024.0:
                    return "%3.1f%s%s" % (num, unit, suffix)
                num /= 1024.0
            return "%.1f%s%s" % (num, 'Yi', suffix)
        try:
            data = {}
            # data entries must be strings so just serialise
            # some more sorcery that i dont fully understand. Connects to googles DNS server
            s = socket(AF_INET, SOCK_DGRAM)
            s.connect(("8.8.8.8",0))
            self.ipaddress = s.getsockname()[0]
            onion_address = ""
            self.logger.debug("Collecting metadata")
            jsondata = {}
            try:
                with open("/home/tor_private/hostname") as f:
                    onion_address=f.read().replace('\n', '')
                jsondata["onion_address"] = onion_address.split(" ")[0]
                jsondata["onion_cookie_auth"] = onion_address.split(" ")[1]
                jsondata["onion_cookie_client"] = onion_address.split(" ")[-1]
            except Exception as e:
                self.logger.warning(str(e))
            if self.last_upload_time == None:
                fullstr = "<h1>"+str(self.cameraname)+"</h1><br>Havent uploaded yet<br> Ip address: "+ self.ipaddress + "<br>onion_address: "+onion_address+"<br><a href='http://" + self.ipaddress + ":5000'>Config</a>" 
            else:
                fullstr = "<h1>"+str(self.cameraname)+"</h1><br>Last upload at: " + self.last_upload_time.strftime("%y-%m-%d %H:%M:%S") + "<br> Ip address: "+ self.ipaddress + "<br>onion_address: "+onion_address+"<br><a href='http://" + self.ipaddress + ":5000'>Config</a>"
            try:
                a_statvfs = os.statvfs("/")
                free_space = sizeof_fmt(a_statvfs.f_frsize*a_statvfs.f_bavail)
                total_space = sizeof_fmt(a_statvfs.f_frsize*a_statvfs.f_blocks)
                jsondata["name"]=self.cameraname
                jsondata["tb_uploaded"] = self.total_data_uploaded_tb
                jsondata["smaller_uploaded"] = sizeof_fmt(self.total_data_uploaded_b)
                jsondata["interval"]=self.config.get("timelapse","interval")
                jsondata["upload_check_interval"] = self.timeinterval
                jsondata["free_space"]=free_space
                jsondata["total_space"]=total_space
                jsondata["serialnumber"] = self.config_filename[:-4].split("/")[-1]
                jsondata["ip_address"] = self.ipaddress
                jsondata["list_of_uploads"] = list_of_uploads
                jsondata["capture_limits"]= self.config.get('timelapse','starttime') +" - "+ self.config.get('timelapse', 'stoptime')
                # need to check against none because otherwise it gets stuck in a broken loop.
                if not self.last_upload_time == None:
                	epoch = datetime.datetime.utcfromtimestamp(0)
                	delta = self.last_upload_time - epoch
                	jsondata["last_upload_time"] = delta.total_seconds()
                jsondata["version"] = subprocess.check_output(["/usr/bin/git describe --always"], shell=True)
            except Exception as e:
                self.logger.error("Couldnt upload metadata: %s" % str(e))
            data["metadata.json"] = json.dumps(jsondata, indent=4, sort_keys=True)
            data["ipaddress.html"] = fullstr
            self.logger.debug("Sending metadata to server now")
            if not self.sendMetadataSFTP(data):
                self.sendMetadataFTP(data)
        except Exception as e:
            self.logger.error(str(e))
            time.sleep(5)

    def run(self):
        """ Main upload loop
        """
        while(True):
            # sleep for a while
            time.sleep(self.timeinterval)
            # check and see if config has changed.
            if os.stat(self.config_filename).st_mtime!=self.last_config_modify_time:
                # reset last change time to last and setup()
                self.last_config_modify_time = os.stat(self.config_filename).st_mtime
                self.setup()
            try:
                upload_list = glob(os.path.join(self.upload_directory,'*'))
                self.set_metadata_on_server(upload_list)
                
                if (len(upload_list)==0):
                    self.logger.debug("No files in upload directory")
                    
                if (len(upload_list) > 0) and self.config.get("ftp","uploaderenabled")=="on":
                    self.logger.debug("Preparing to upload %d files" % len(upload_list))
                    if not self.sftpUpload(upload_list):
                        self.ftpUpload(upload_list)
                    self.last_upload_time = datetime.datetime.now()
            except Exception as e:
                self.logger.error("ERROR: UPLOAD %s" % str(e))

class FtpUploadTracker:
    sizeWritten = 0
    totalSize = 0
    lastShownPercent = 0
    multiple = 0 
   
    def __init__(self, totalSize):
        self.totalSize = totalSize
        self.multiple = round(self.totalSize / 100)

    def handle(self, block):
        self.sizeWritten+=1024
        if self.sizeWritten > self.multiple:
            percentage = round((self.sizeWritten / self.totalSize)*100)
            sys.stderr.write('\r[{0}] {1}%'.format('.'*int(percentage),int(percentage)))
            self.lastShownPercent+=self.multiple
            sys.stderr.flush()



class Scheduler(Thread):
    """ Scheduler class,
        used to schedule events,
    """
    def __init__(self,config, port, name = None):
        # same thread name hackery that the Camera threads use
        if name == None:
            Thread.__init__(self)
        else:
            Thread.__init__(self, name=name)
        # get a logger first
        self.logger = logging.getLogger(self.getName())
        self.last_schedule_mod_time = ""
        # and the same setup stuff that they use as well.
        self.logger.info("Starting up scheduler")
        self.camera_port = port
        self.schedule_file_name = "schedules/"+self.name+".p"
        self.config = SafeConfigParser()
        self.config.read(config)
        if not os.path.isfile(self.schedule_file_name):
            cPickle.dump([],open(self.schedule_file_name,'w'))
            self.logger.info("Creating new pickle")
        self.setup()

    def setup(self):
        # load data
        self.unordered_sched = cPickle.load(open(self.schedule_file_name,'rb'))
        self.ordered_sched = sorted(self.unordered_sched)
        self.logger.info(str(len(self.ordered_sched))+" jobs to do")
        # set job number to 0 and cycle through the joblist until we get to the time now.
        self.job_number = 0
        tn = datetime.datetime.now()
        self.interval = self.config.getint("timelapse","interval")
        for a_time, value in self.ordered_sched:
            if datetime.time(int(a_time[:2]),int(a_time[3:])) < tn.time():
                self.job_number += 1
        tries = 0
        if len(self.ordered_sched)>0:
            self.job_number -=1 
            while not self.do_job() and not tries > 5:
                time.sleep(15)
                tries += 1
            self.job_number +=1

        with open(os.path.join("schedules",self.name + ".cfglist"),'wb') as file:
            try:
                cfg_list = subprocess.check_output("gphoto2 --port "+self.camera_port+" --list-all-config",shell=True)
                file.write(cfg_list)
            except subprocess.CalledProcessError as error:
                self.logger.error("Couldnt get the current config state for the webui, is the camera connected? : %s" % str(error))

    def do_job(self):
        try:
            self.logger.info("Beginning job #"+str(self.job_number))
            config_string = ""
            for st,val in self.ordered_sched[self.job_number][1]:
                config_string+=" --set-config-index "+st+"="+val
            self.logger.info("Current job parameters: %s" % config_string)
            output = subprocess.check_output("gphoto2 --port "+self.camera_port +" "+config_string,universal_newlines=True, shell=True)
            return True
        except subprocess.CalledProcessError as error:
            self.logger.error("Couldnt do job, gonna try again: %s" % str(error.output))
            return False
            

    def time2seconds(self, t):
        """ Convert the time to seconds
            TODO: a better implementation of this such as datetime.timesinceepoch or some sorcery
        """
        return t.hour*60*60+t.minute*60+t.second
        
    def run(self):
        while True:
            try:
                midnight = datetime.time(23,59)
                next_time_obj = midnight
                tn = datetime.datetime.now()
                # sleep through the midnight minute.
                if tn.time() >datetime.time(23,59):
                    self.logger.info("Its Midnight, sleeping until tomorrow morning")
                    time.sleep(130)
                
                if os.stat(self.schedule_file_name).st_mtime!=self.last_schedule_mod_time:
                        # reset last change time to last and setup()
                        self.logger.info("Change in schedule, setting up again")
                        self.last_schedule_mod_time = os.stat(self.schedule_file_name).st_mtime
                        self.setup()
                
                if self.job_number<len(self.ordered_sched) and self.job_number >=0:
                    next_time_string = self.ordered_sched[self.job_number][0]
                    next_time_obj = datetime.time(int(next_time_string[:2]),int(next_time_string[3:]))

                if self.job_number>=len(self.ordered_sched):
                    next_time_obj = midnight
                    self.job_number = -1
                    self.logger.info("Last job for the period, setting job number to minus one.")
                
                if tn.time() > next_time_obj:
                    if self.job_number >= 0:
                        #self.logger.info("waiting so that i dont start at the same time as the other")
                        #if self.time2seconds(next_time_obj) % self.interval < 5:
                        #    time.sleep(50)
                        while not self.do_job():
                            time.sleep(5)
                    self.job_number += 1
                time.sleep(1)
                    
            except Exception as e:
                self.logger.error("Something went wrong: %s" % str(e))

      
def detect_cameras(type):
    """ 
    detect cameras:
        args= string
            - type to search for in the output of gphoto2, enables the searching of serial cameras and maybe webcams.
    """
    try:
        a = subprocess.check_output("gphoto2 --auto-detect", shell=True)
        cams = {}
        for port in re.finditer("usb:", a):
            cmdret = subprocess.check_output('gphoto2 --port "'+a[port.start():port.end()+7]+'" --get-config serialnumber', shell=True)
            cams[a[port.start():port.end()+7]] = cmdret[cmdret.find("Current: ")+9: len(cmdret)-1]
        return cams
    except Exception as e:
        logger.error("Could not detect camera for some reason: %s" % str(e))


def redetect_cameras(camera_workers):
    """
    this isnt used. but might be, if you want to change stuff of the cameras threads.

    """
    try:
        a = subprocess.check_output("gphoto2 --auto-detect", shell=True)
        for port in re.finditer("usb:", a):
            cmdret = subprocess.check_output('gphoto2 --port "'+a[port.start():port.end()+7]+'" --get-config serialnumber', shell=True)
            serialnumber = cmdret[cmdret.find("Current: ")+9: len(cmdret)-1]
            port = a[port.start():port.end()+7]
            for camera_worker in camera_workers:
                if camera_worker.__name__ == serialnumber:
                    camera_worker.camera_port = port
                    logger.info("redetected camera: "+str(serialnumber)+" : "+str(port))
        return True
    except Exception as e:
        print str(e)
        logger.error("Could not detect camera for some reason: " + str(e))
        return False


def detect_picam():
    try:
        cmdret = subprocess.check_output("/opt/vc/bin/vcgencmd get_camera", shell=True)
        if cmdret[cmdret.find("detected=")+9 : len(cmdret)-1] == "1":
            return True
        else:
            return False
    except subprocess.CalledProcessError:
        return False

def create_workers(cameras):
    camthreads = []
    uploadthreads = []
    schedulerthreads = []
    for port,serialnumber in cameras.iteritems():
        camthreads.append(Camera(os.path.join("configs_byserial",serialnumber+".ini"), camera_port=port,serialnumber=serialnumber,name=serialnumber))
        uploadthreads.append(Uploader(os.path.join("configs_byserial", serialnumber+".ini"), name=serialnumber+"-Uploader"))
        schedulerthreads.append(Scheduler(os.path.join("configs_byserial", serialnumber+".ini"),port,name=serialnumber+"-Scheduler"))
    return (camthreads, uploadthreads, schedulerthreads)

def start_workers(objects):
    for thread in objects:
        thread.daemon = True
        thread.start()

def kill_workers(objects):
    for thread in objects:
        thread.join()

def get_usb_dev_list():
    context = pyudev.Context()
    ret = ""
    for device in context.list_devices(subsystem='usb'):
        ret+=str(device)

if __name__ == "__main__":
    logger = logging.getLogger("Worker_dispatch")
    logger.debug("Program startup")
    #The main loop for capture 
    #TODO: Fix storage for multiple cameras, add the picam detection in.
    try:
        tries = 0
        cameras = None
        has_picam = detect_picam()
        while cameras == None and tries < 10:
            logger.debug("detecting Cameras")
            cameras = detect_cameras("usb")
            tries+=1
            
        if not cameras == None:
            workers = create_workers(cameras)
            start_workers(workers[0])
            start_workers(workers[1])
            start_workers(workers[2])
        
        if has_picam:
            raspberry = [PiCamera("picam.ini", name="PiCam"), Uploader("picam.ini", name="PiCam-Uploader")]
            start_workers(raspberry)

        usb_dev_list = get_usb_dev_list()
        while True:
            if usb_dev_list != get_usb_dev_list(): 
                cameras= detect_cameras("usb")
                kill_workers(workers[0])
                kill_workers(workers[1])
                kill_workers(workers[2])
                # start workers again
                time.sleep(60)
                workers = create_workers(cameras)
                start_workers(workers[0])
                start_workers(workers[1])
                start_workers(workers[2])
                usb_dev_list = get_usb_dev_list()
            time.sleep(1)

    except (KeyboardInterrupt, SystemExit):
        if not cameras == None:
            kill_workers(workers[0])
            kill_workers(workers[1])
            kill_workers(workers[2])
        if has_picam:
            kill_workers(raspberry)
            
        sys.exit()
