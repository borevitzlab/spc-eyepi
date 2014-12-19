#!/usr/bin/python
from __future__ import division
import os, subprocess, sys, platform, io
import datetime, time, shutil
import pysftp, ftplib
import logging, logging.config
from glob import glob
from ConfigParser import SafeConfigParser
from optparse import OptionParser
from threading import Thread
from socket import socket, SOCK_DGRAM, AF_INET 

# Global configuration variables
config_filename = 'eyepi.ini'
timestartfrom = datetime.time.min
timestopat = datetime.time.max
default_extension = ".JPG"
#Acceptable filtypes, DONT INCLUDE JPG!!!
filetypes = ["CR2","RAW","NEF","JPG","JPEG"]
global birthday
birthday = datetime.datetime(1990, 07,17,12,12,12,13)
logging.config.fileConfig(config_filename)
logging.getLogger("paramiko").setLevel(logging.WARNING)

def timestamp(tn):
    """ Build a timestamp in the required format
    """
    st = tn.strftime('%Y_%m_%d_%H_%M_%S')
    return st


class Camera(Thread):
    def __init__(self, config_filename, name = None):
        if name == None:
            Thread.__init__(self)
        else:
            Thread.__init__(self, name=name)

        self.last_config_modify_time = None
        self.config_filename = config_filename
        self.logger = logging.getLogger(self.getName())
        self.setup()
        
    def setup(self):
        self.config = SafeConfigParser()
        self.config.read(self.config_filename)
        self.accuracy = 3
        self.cameraname = self.config.get("camera","name")
        self.interval = self.config.getint("timelapse","interval")
        self.spool_directory = self.config.get("localfiles","spooling_dir")
        self.upload_directory = self.config.get("localfiles","upload_dir")
        
        if self.config.get("camera","enabled")=="on":
            self.is_enabled= True
        else:
            self.is_enabled = False
        try:
            tval = self.config.get('timelapse','starttime')
            if len(tval)==5:
                if tval[2]==':':
                    self.timestartfrom = datetime.time(int(tval[:2]),int(tval[3:]))
                    self.logger.debug("Starting at %s" % self.timestartfrom.isoformat())
        except Exception, e:
            self.logger.error("Time conversion error startime - %s" % str(e))
        try:
            tval = self.config.get('timelapse','stoptime')
            if len(tval)==5:
                if tval[2]==':':
                    self.timestopat = datetime.time(int(tval[:2]),int(tval[3:]))
                    self.logger.debug("Stopping at %s" % self.timestopat.isoformat())
        except Exception, e:
            self.logger.error("Time conversion error startime - %s" % str(e))
        
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
                        self.logger.debug("Deleting previous file in the spool eh, Sorry.")
                except Exception, e:
                    self.logger.error("Sorry, buddy! Couldn't delete the files in spool, eh! Error: %s" % e)

        if not os.path.exists(self.upload_directory):
            self.logger.info("creating copyfrom dir %s" % self.upload_directory)
            os.makedirs(self.upload_directory)
            
    def timestamped_imagename(self,timen):
        """ Build the pathname for a captured image.
        """
        return os.path.join(self.upload_directory, self.cameraname + '_' + timestamp(timen) + default_extension)

    def time2seconds(self, t):
        return t.hour*60*60+t.minute*60+t.second

    
    def run(self):
        self.next_capture = datetime.datetime.now()
        # this is to test and see if the config has been modified
        self.last_config_modify_time = None
        while (True):
            # testing for the config modification
            if os.stat(self.config_filename).st_mtime!=self.last_config_modify_time:
                self.last_config_modify_time = os.stat(config_filename).st_mtime
                # Resetup()
                self.setup()
                self.logger.debug("change in config at "+ datetime.datetime.now().isoformat() +" reloading")
            
            # set a timenow
            tn = datetime.datetime.now()
            
            # This is used to check and see if the date is smething ridiculous.
            # Log if the time isn't sane yet (needs to get it from ntpdate)
            if tn<birthday:
                self.logger.error("My creator hasnt been born yet, sleeping until the time comes...")
                time.sleep(30)
            # checking if enabled and other stuff
            if (tn>birthday) and (self.time2seconds(tn)%self.interval< self.accuracy) and (tn.time() > self.timestartfrom) and (tn.time() < self.timestopat) and (self.is_enabled):
                try:
                    self.next_capture = tn + datetime.timedelta(seconds = self.interval)
                        
                    # The time now is within the operating times
                    self.logger.info("Capturing Image, now: %s"  % tn.isoformat())
                    
                    # setting variables for saving files
                    raw_image = self.timestamped_imagename(tn)
                    jpeg_image = self.timestamped_imagename(tn)[:-4]+".jpg"
                    
                    
                    #TODO:
                    #1. check for the camera capture settings/config file
                    #2. put other camera settings in another call to setup camera (iso, aperture etc) using gphoto2 --set-config

                    # No conversion needed, just take 2 files, 1 jpeg and 1 raw
                    cmd = ["gphoto2 --set-config capturetarget=sdram --capture-image-and-download --filename='"+os.path.join(self.spool_directory, os.path.splitext(raw_image)[0])+".%C'"]
                    
                    # subprocess.call. shell=True is hellishly insecure and doesn't throw an error if it fails. Needs to be fixed somehow <shrug>
                    output = subprocess.check_output(cmd,stderr=subprocess.STDOUT, shell=True)
                    self.logger.info("GPHOTO: "+ output)
                    self.logger.info("Capture Complete")
                    self.logger.info("Moving and renaming image files, buddy")

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
                            # best to create a symlink to /dev/shm/ from static/
                            shutil.copy(file,os.path.join("static","temp", "dslr_last_image.jpg"))
                            if self.config.get("ftp","uploadwebcam") == "on":
                                shutil.copy(file,os.path.join(self.upload_directory, "dslr_last_image.jpg"))
                        # move timestamped image te be uploaded
                        if config.get("ftp","uploadtimestamped")=="on":
                            self.logger.info("saving timestamped image for you, buddy")
                            os.rename(file, os.path.join(self.upload_directory, os.path.basename(name+ext)))
                        else:
                            self.logger.info("deleting file, eh")
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

            time.sleep(0.01)

class PiCamera(Camera):
    def run(self):
        self.next_capture = datetime.datetime.now()
        # this is to test and see if the config has been modified
        self.last_config_modify_time = None
        while (True):
            # testing for the config modification
            if os.stat(self.config_filename).st_mtime!=self.last_config_modify_time:
                self.last_config_modify_time = os.stat(self.config_filename).st_mtime
                # Resetup()
                self.setup()
                self.logger.debug("change in config at "+ datetime.datetime.now().isoformat() +" reloading")
            
            # set a timenow
            tn = datetime.datetime.now()
            # This is used to check and see if the date is something ridiculous.
            # Log if the time isn't sane yet (needs to get it from ntpdate)
            if tn<birthday:
                self.logger.error("My creator hasnt been born yet, sleeping until the time comes...")
                time.sleep(30)
            # checking if enabled and other stuff
            if (tn>birthday) and (self.time2seconds(tn)%self.interval< self.accuracy) and (tn.time() > self.timestartfrom) and (tn.time() < self.timestopat) and (self.is_enabled):
                try:
                    self.next_capture = tn + datetime.timedelta(seconds = self.interval)
                        
                    # The time now is within the operating times
                    self.logger.info("Capturing Image, now: %s"  % tn.isoformat())
                    
                    image_file = self.timestamped_imagename(tn)

                    # take the image using os.system(), pretty hacky but it cant exactly be run on windows.
                    os.system("raspistill --nopreview -o "+image_file)
                    self.logger.info("Capture Complete")
                    self.logger.info("Copying the image to the web service, buddy")
                    # Copy the image file to the static webdir 
                    shutil.copy(image_file,os.path.join("static","temp","pi_last_image.jpg"))
                    # webcam copying
                    if self.config.get("ftp","uploadwebcam") == "on":
                        shutil.copy(image_file,os.path.join(self.upload_directory, "pi_last_image.jpg"))
                    # rename for timestamped upload
                    if self.config.get("ftp","uploadtimestamped")=="on":
                        self.logger.info("saving timestamped image for you, buddy")
                        os.rename(image_file ,os.path.join(self.upload_directory,os.path.basename(image_file))) 
                    else:
                        self.logger.info("deleting file buddy")
                        os.remove(file)

                    if self.next_capture.time() < self.timestopat:
                        self.logger.info("Next capture at %s" % self.next_capture.isoformat())
                    else:
                        self.logger.info("Capture will stop at %s" % self.timestopat.isoformat())
                        
                except Exception, e:
                    self.next_capture = datetime.datetime.now()
                    # TODO: This needs to catch errors from subprocess.call because it doesn't
                    self.logger.error("Image Capture error - " + str(e))

            time.sleep(0.01)
        

class Uploader(Thread):
    def __init__(self, config_filename, name = None):
        if name == None:
            Thread.__init__(self)
        else:
            Thread.__init__(self, name=name)
            
        self.last_config_modify_time = None
        self.config_filename = config_filename
        self.logger = logging.getLogger(self.getName())
        
        self.setup()

    def setup(self):
        self.timeinterval = 60
        self.uploadtimedelay = 1
        self.config = SafeConfigParser()
        self.config.read(self.config_filename)
        self.hostname = self.config.get("ftp","server")
        self.user = self.config.get("ftp","user")
        self.passwd = self.config.get("ftp","pass")
        self.target_directory = self.config.get("ftp", "directory")
        self.cameraname = self.config.get("camera","name")
        self.upload_directory = self.config.get("localfiles","upload_dir")
        self.last_upload_time = None
        self.ipaddress = None

    def makeserveripaddressSFTP(self,thisip):
        try:
            self.logger.info("trying to store new ip on server using SFTP, friend!")
            link = pysftp.Connection(host=self.hostname, username=self.user, password=self.passwd)
            link.chdir("/")
            self.mkdir_p_sftp(link, os.path.join(self.target_directory,self.cameraname) )
            f = link.open(os.path.join(self.target_directory,self.cameraname,"ipaddress.html"), mode='w')
            f.write(thisip)
            f.close()
        except Exception as e:
            self.logger.error("SFTP:  "+ str(e))
            return False
        return True
        

    def makeserveripaddressFTP(self,thisip):
        try:
            self.logger.info("trying to store new ip on server using FTP, friend!")
            ftp = ftplib.FTP(self.hostname)
            ftp.login(self.user,self.passwd)
            self.mkdir_p_ftp(ftp, os.path.join(self.target_directory,self.cameraname))
            unicodeip = unicode(thisip)
            assert isinstance(unicodeip, unicode)
            file = io.BytesIO(unicodeip.encode("utf-8"))
            ftp.storbinary('STOR ipaddress.html',file)
            ftp.quit()
        except Exception as e:
            self.logger.error(str(e))
            return False
        return True

    def sftpUpload(self,filenames):
        """ Secure upload the image file to the Server
        """
        try:
            self.logger.info("Connecting sftp and uploading buddy")
            link = pysftp.Connection(host=self.hostname, username=self.user, password=self.passwd)
            link.chdir("/")
            self.mkdir_p_sftp(link, os.path.join(self.target_directory,self.cameraname))
            self.logger.info("Uploading")
            for f in filenames:
                link.put(f,os.path.basename(f), callback=self.sftpuploadtracker)
                os.remove(f)
                self.logger.debug("Successfuly uploaded %s through sftp and removed from local filesystem" % f)
            self.logger.info("Disconnecting, eh")
            link.close()
        except Exception as e:
            self.logger.error("SFTP:  " + str(e))
            return False
        return True
      

    def ftpUpload(self, filenames):
        """ insecure upload for backwards compatibility
        """
        self.logger.info("Looks like I can't make a connection using sftp, eh. Falling back to ftp.")
        try:
            self.logger.debug("Connecting ftp")
            ftp = ftplib.FTP(self.hostname)
            ftp.login(self.user,self.passwd)
            self.mkdir_p_ftp(ftp, os.path.join(self.target_directory,self.cameraname))
            self.logger.info("Uploading")                
            for f in filenames:
                totalSize = os.path.getsize(f)
                uploadTracker = FtpUploadTracker(totalSize)
                ftp.storbinary('stor '+ os.path.basename(f), open(f, 'rb'), 1024, uploadTracker.handle)
                self.logger.debug("Successfuly uploaded %s through ftp and removed from local filesystem" % f)
                os.remove(f)
        except Exception as e:
            self.logger.error(str(e))
            return False
        return True

    def mkdir_p_sftp(self, sftp, remote_directory):
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
            self.logger.debug("Sorry, just have to make some new directories, eh. (sftp)")
            sftp.mkdir(basename)
            sftp.chdir(basename)

    def mkdir_p_ftp(self, ftp, remote_directory):
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
            self.logger.debug("Sorry, just have to make some new directories, eh. (ftp)")
            ftp.mkd(basename)
            ftp.cwd(basename)

    def sftpuploadtracker(self,transferred, total):
        if total/100 != 0:
            if (transferred % (total/100)):
                percentage = round((transferred / total)*100)
                sys.stderr.write('\r[{0}] {1}%'.format('.'*int(percentage),int(percentage)))
                sys.stderr.flush()

            
    def set_ip_on_server(self, l_last_upload_time):
        try:
            s = socket(AF_INET, SOCK_DGRAM)
            s.connect(("8.8.8.8",0))
            self.ipaddress = s.getsockname()[0]
            if l_last_upload_time == None:
                fullstr = "Havent uploaded yet<br> Ip address: "+ self.ipaddress + "<br><a href='http://" + self.ipaddress + ":5000'>Config</a>" 
            else:
                fullstr = "Last upload at: " + l_last_upload_time.strftime("%y-%m-%d %H:%M:%S") + "<br> Ip address: "+ self.ipaddress + "<br><a href='http://" + self.ipaddress + ":5000'>Config</a>"
            self.logger.info("my IP address:" + str(self.ipaddress))
            if not self.makeserveripaddressSFTP(fullstr):
                self.makeserveripaddressFTP(fullstr)
        except Exception as e:
            self.logger.error(str(e))
            time.sleep(5)
                              

    def run(self):
        while(True):

            if self.config.get("ftp","uploaderenabled")=="on":
                self.logger.info("Waiting %d secs to check directories again" % self.timeinterval)
            time.sleep(self.timeinterval)
            
            if os.stat(self.config_filename).st_mtime!=self.last_config_modify_time:
                self.last_config_modify_time = os.stat(self.config_filename).st_mtime
                self.setup()
               
            self.set_ip_on_server(self.last_upload_time)
            upload_list = glob(os.path.join(self.upload_directory,'*'))
            
            if (len(upload_list)==0):
                self.logger.info("no files in upload directory")
                
            if (len(upload_list) > 0) and self.config.get("ftp","uploaderenabled")=="on":
                self.logger.debug("Pausing %d seconds to wait for files to be closed" % self.uploadtimedelay)
                time.sleep(self.uploadtimedelay)
                self.logger.debug("Preparing to upload %d files" % len(upload_list))
                if not self.sftpUpload(upload_list):
                    self.ftpUpload(upload_list)
                self.last_upload_time = datetime.datetime.now()

        

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
            




if __name__ == "__main__":
    
    #The main loop for capture 
    #TODO: Objectify camera object and incorporate picam, threading etc.
    
    try:
        camera1 = Camera("eyepi.ini", name="DSLR1")  
        upload1 = Uploader("eyepi.ini", name="DSLR1-Uploader")
        #raspberrycam = PiCamera("picam.ini", name="PiCamera")
        #raspberryupload = Uploader("picam.ini", name="PiCamera-Uploader")
                                
        #raspberrycam.daemon = True
        #raspberryupload.daemon = True
        upload1.daemon = True
        camera1.daemon = True
                                
        #raspberrycam.start()
        #raspberryupload.start()                  
        upload1.start()
        camera1.start()
                                
        while True:time.sleep(100)
    except (KeyboardInterrupt, SystemExit):
        sys.exit()
        camera1.join()
        upload1.join()
        raspberrycam.join()
        raspberryupload.join()
    

