
Ansible Deployment
##################

Requirements
------------

.. highlight:: console

You need `Ansible <https://www.ansible.com/>`_ version 2.3 installed locally

.. code-block:: bash

    # pip install git+https://github.com/ansible/ansible

You must also have the sshpass program (according to ansible), this is ussed only for first setup of ssh keyfile access.

if you are using ArchLinux

.. code-block:: bash

    # pacman -S sshpass

or if you are using Ubuntu/Debian

.. code-block:: bash

    # apt-get install sshpass


Setup
-----

Add add your ssh public key to the *ansible/keys* folder (you can also delete ours from there, unless you **really** trust us).

If you want a local user account make sure there is an entry in *ansible/vars/userlist.yml* and with a reference to your ssh key.
You need to add your ssh key to the list of keys for the user "alarm", as this is the user that ansible needs to install things.


The contents of users file should look something like this:

.. code-block:: yaml
    :caption: *ansible/vars/userlist.yml*
    :name: userlist

    users:
      - {name: alarm,  shell: /bin/bash, groups: [wheel, adm, users],
          clear_password: yes,
          keys: [your_key.pub]}
      - {name: your_username,  shell: /bin/bash, groups: [wheel, adm, users],
          keys: [your_key.pub]}

After the first login this playbook locks down authentication (including disabling password logins) and doesn't check to see whether an ssh key has been added, so you could lock yourself out.


Traitcapture.org integration
----------------------------

If you have an api key for traitcapture.org, put it in *ansible/secure/api_key.yml* in this format (dont forget the quotation marks!):

.. code-block:: yaml
    :caption: *ansible/secure/api_key.yml*
    :name: api-key

    api_key:
        "your_api_key"


OpenVPN
-------
If you want the RPi to be connected to an openvpn server, add your openvpn profile in to the *secure* directory (*ansible/secure/my_vpn_conf.conf*) and set the *vpn_conf* variable in *eyepi.yml* and it will copy and activate/enable that profile.


Setting up the Raspberry Pi
---------------------------

Create an SD card with the latest Archlinux Arm (see the `installation guide <https://archlinuxarm.org/platforms/armv8/broadcom/raspberry-pi-3#installation>`_)

Log into the raspberry pi using ssh with the username:password *alarm:alarm* and install python2.


The RPi doesn't have **sudo** installed yet, so you must use **su**. The root password is *root*:

.. code-block:: bash

    [alarm@alarm ~]$ su
    [root@alarm ~]$ pacman -Syy python2


Add the ip address of the RPi to the list in the *ansible/hosts* file so that it looks like this:

.. code-block:: guess
    :caption: *ansible/hosts*
    :name: hosts

    [rpis]
    your_rpi_ip_address

    [rpis:vars]
    ansible_user=alarm
    ansible_password=alarm
    become_user=root
    ansible_become=yes
    ansible_become_method=su
    ansible_become_pass=root
    ansible_python_interpreter=/usr/bin/python2


If you would like to assign the RPi a persistent hostname (that isnt its ip address) you must have something to identify it by.

CPU serial number:

.. code-block:: bash

    [alarm@alarm ~]$ grep -Eor "Serial.*([[:xdigit:]])" /proc/cpuinfo | cut -d " " -f2

Machine id:

.. code-block:: bash

    [alarm@alarm ~]$ cat /etc/machine-id

You can use either of those to create a new entry in the *ansible/vars/hostmap.yml* using this syntax:

.. code-block:: yaml
    :caption: *ansible/vars/hostmap.yml*
    :name: hostmap

    hostnames:
        your_machine_id: "your_desired_hostname"

Run the play

.. code-block:: bash

    $ ansible-playbook -i hosts eyepi.yml

You can use the same command to update the software on the RPi.

The play builds libgphoto2 and gphoto2 from source, which can take a while, you can skip this process by using the
:code:`--skip-tags gphoto2` directive. You can also run a specific tags with the :code:`--tags [tag,...]` directive.
The full list of tags that can be used are in *ansible/eyepi.yml*.
