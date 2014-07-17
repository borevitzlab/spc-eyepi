import subprocess

def camera_fs_mounted():

    camera_mount_id = 'Mount(1): Canon Digital Camera'
    camera_usb_mountpoint = None
    
    gvfs_info = subprocess.check_output(['gvfs-mount', '-l'])
    # print('gvfs-mount said:', py2output)
    
    if camera_mount_id in gvfs_info:
    
        # print( "Camera is mounted" )
        
        gvfs_lines = gvfs_info.split('\n')
        
        for l in gvfs_lines:
            # print( l )
            if l.startswith(camera_mount_id):
                # Look for -> gphoto2://[usb:002,018]/\n
                print( "Camera-FS is mounted at >> %s << " % l )
                camera_usb_mountpoint = 'gphoto2://[' + l[l.find('usb:'):l.find(']')] + ']'
#    else:
#        print("Camera-FS is not mounted (Good)")
        
    return camera_usb_mountpoint
    

def camera_fs_unmount():

    gphoto_mountpoint = camera_fs_mounted()
    
    if not gphoto_mountpoint is None:

        gvfs_info = subprocess.check_output(['gvfs-mount', '-u',gphoto_mountpoint])
        
        if gvfs_info is None:
            print("Unmounted")
        
    return True

if __name__ == "__main__":

    print camera_fs_unmount()


