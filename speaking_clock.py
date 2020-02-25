#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
speaking clock
'''

import argparse
import configparser
import copy
import datetime
#import hashlib
import json
import os
#import stat
import signal
import sys
import subprocess
import time
from threading import Event, Thread
import select
import tty
import collections
import termios
import requests
import urllib

# requires making code less readable:
# Xpylint:disable=bad-whitespace
# pylint:disable=too-many-branches
# pylint:disable=too-many-locals
# Xpylint:disable=too-many-nested-blocks
# Xpylint:disable=too-many-statements
# pylint:disable=global-statement

# broken in pylint3:
# pylint:disable=global-variable-not-assigned

##########################################################################################

URL_GITHUB_HASH_SELF = 'https://api.github.com/repos/speculatrix/speaking_clock/speaking_clock.py'

GOOGLE_TTS = 'http://translate.google.com/translate_tts?ie=UTF-8&client=tw-ob&tl=en&q='
G_TTS_UA = 'VLC/3.0.2 LibVLC/3.0.2'

# the settings file is stored in a directory under $HOME
SETTINGS_DIR = '.speaking_clock'
SETTINGS_FILE = 'settings.ini'
SETTINGS_SECTION = 'user'

TITLE = 'title'
TS_PLAY = 'ts_play'
DFLT = 'default'
HELP = 'help'

SETTINGS_DEFAULTS = {
    TS_PLAY: {
        TITLE: 'Player',
        DFLT: '/usr/bin/omxplayer.bin -o alsa',
        #DFLT: 'vlc -I dummy --novideo',
        HELP: 'Command to play media with arguments, try "/usr/bin/omxplayer.bin -o alsa" or "vlc -I dummy --novideo --play-and-exit"',
    },
}


##########################################################################################
# help
def print_help():
    '''prints help'''

    print('''=== Help
? - help
h - help
q - quit
t - speak time
''')


##########################################################################################
def text_to_speech_file(input_text, output_file):
    '''uses Google to turn supplied text into speech in the file'''

    goo_url = '%s%s' % (GOOGLE_TTS, urllib.parse.quote(input_text), )
    opener = urllib.request.build_opener()
    opener.addheaders =[('User-agent', G_TTS_UA), ]

    write_handle = open(output_file, 'wb')
    with opener.open(goo_url) as goo_handle:
        write_handle.write(goo_handle.read())


##########################################################################################
def check_load_config_file(settings_dir, settings_file):
    '''check there's a config file which is writable;
       returns 0 if OK, -1 if the rest of the page should be aborted,
       > 0 to trigger rendering of the settings page'''

    global DBG_LEVEL
    global MY_SETTINGS

    ########
    if os.path.isfile(settings_dir):
        error_text = 'Error, "%s" is a file and not a directory' % (settings_dir, )
        return (-2, error_text)

    if not os.path.isdir(settings_dir):
        os.mkdir(settings_dir)
        if not os.path.isdir(settings_dir):
            error_text = 'Error, "%s" is not a directory, couldn\'t make it one' % (settings_dir, )
            return (-2, error_text)


    # verify the settings file exists and is writable
    if not os.path.isfile(settings_file):
        error_text = 'Error, can\'t open "%s" for reading' % (settings_file, )
        return(-1, error_text)

    # file is zero bytes?
    config_stat = os.stat(settings_file)
    if config_stat.st_size == 0:
        error_text = 'Error, "%s" file is empty\n' % (settings_file, )
        return(-1, error_text)

    if not MY_SETTINGS.read(settings_file):
        error_text = 'Error, failed parse config file "%s"' % (settings_file, )
        return(-1, error_text)

    return (0, 'OK')



##########################################################################################
# settings_editor
def settings_editor(settings_dir, settings_file):
    '''settings_editor'''

    global DBG_LEVEL
    global MY_SETTINGS

    if SETTINGS_SECTION not in MY_SETTINGS.sections():
        print('section %s doesn\'t exit' % SETTINGS_SECTION)
        MY_SETTINGS.add_section(SETTINGS_SECTION)

    print('=== Settings ===')

    # attempt to find the value of each setting, either from the params
    # submitted by the browser, or from the file, or from the defaults
    for setting in SETTINGS_DEFAULTS:
        setting_value = ''

        try:
            setting_value = str(MY_SETTINGS.get(SETTINGS_SECTION, setting))
        except configparser.NoOptionError:
            if DFLT in SETTINGS_DEFAULTS[setting]:
                setting_value = SETTINGS_DEFAULTS[setting][DFLT]
            else:
                setting_value = ''

        print('Hint: %s' % (SETTINGS_DEFAULTS[setting][HELP], ))
        print('%s [%s]: ' % (SETTINGS_DEFAULTS[setting][TITLE], setting_value, ), end='')
        sys.stdout.flush()
        new_value = sys.stdin.readline().rstrip()
        if new_value != '' and new_value != '\n':
            MY_SETTINGS.set(SETTINGS_SECTION, setting, new_value)
        else:
            MY_SETTINGS.set(SETTINGS_SECTION, setting, setting_value)
        print('')

    config_file_handle = open(settings_file, 'w')
    if config_file_handle:
        MY_SETTINGS.write(config_file_handle)
    else:
        print('Error, failed to open and write config file "%s"' %
              (settings_file, ))
        exit(1)


##########################################################################################
def play_time():

    now = datetime.datetime.now()
    the_time_is = now.strftime('the time is %M minutes past %H, on %b %d, %Y')
    time_file = os.path.join(os.path.join(os.environ['HOME'], SETTINGS_DIR, 'time_file.mp3'))
    text_to_speech_file(the_time_is, time_file)
    play_file(time_file)


##########################################################################################
def play_file(audio_file_name):

    global DBG_LEVEL
    global MY_SETTINGS

    play_cmd = MY_SETTINGS.get(SETTINGS_SECTION, TS_PLAY)
    play_cmd_array = play_cmd.split()
    play_cmd_array.append(audio_file_name)
    #print('Debug, play command is "%s"' % (' : '.join(play_cmd_array), ))

    subprocess.call(play_cmd_array)


##########################################################################################
# SIGINT/ctrl-c handler
def sigint_handler(_signal_number, _frame):
    '''called when signal 2 or CTRL-C hits process'''

    global DBG_LEVEL
    global QUIT_FLAG
    global EVENT
    print('\nCTRL-C QUIT')
    QUIT_FLAG = True
    EVENT.set()


##########################################################################################
def keyboard_listen_thread(event):
    '''keyboard listening thread, sets raw input and uses sockets to
       get single key strokes without waiting, triggering an event.'''

    global QUIT_FLAG
    global KEY_STROKE

    # set term to raw, so doesn't wait for return
    old_settings = termios.tcgetattr(sys.stdin)
    tty.setcbreak(sys.stdin.fileno())

    while QUIT_FLAG == 0:
        # we need a timeout just so's we occasionally check QUIT_FLAG
        readable_sockets, _o, _e = select.select([sys.stdin], [], [], 0.2)
        if readable_sockets:
            KEY_STROKE = sys.stdin.read(1)
            EVENT.set()

    # set term back to cooked
    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)


##########################################################################################
def clock_app():
    '''this runs the clock application'''

    global MY_SETTINGS
    global RADIO_MODE
    global EVENT
    global QUIT_FLAG
    global KEY_STROKE
    global STOP_PLAYBACK

    signal.signal(signal.SIGINT, sigint_handler)

    threads = []
    threads.append(Thread(target=keyboard_listen_thread, args=(EVENT, )))
    threads[-1].start()

    # SIGINT and keyboard strokes and (one day) GPIO events all get funnelled here
    print('clock app waiting on event')
    while not QUIT_FLAG:
        EVENT.wait() # Blocks until the flag becomes true.
        #print('Wait complete')
        if KEY_STROKE != '':
            if KEY_STROKE == 'q':
                print('Quit!')
                QUIT_FLAG = 1

            elif KEY_STROKE == '?' or KEY_STROKE == 'h':
                print_help()

            #elif KEY_STROKE == 'l':
                #DBG_LEVEL and print('list')
                #print('list')
                #print(', '.join(chan_names))

            elif KEY_STROKE == 't':
                play_time()


            else:
                print('Unknown key')

            KEY_STROKE = ''

        EVENT.clear() # Resets the flag.

    for thread in threads:
        thread.join()


##########################################################################################
def main():
    '''the main entry point'''

    DBG_LEVEL = 0

    global SETTINGS_DIR
    global SETTINGS_FILE
    global MY_SETTINGS
    global EVENT
    global KEY_STROKE

    # settings_file is the fully qualified path to the settings file
    settings_dir = os.path.join(os.environ['HOME'], SETTINGS_DIR)
    settings_file = os.path.join(settings_dir, SETTINGS_FILE)
    (config_bad, error_text) = check_load_config_file(settings_dir, settings_file)

    parser = argparse.ArgumentParser()
    parser.add_argument('-d', '--debug', required=False,
                        action="store_true", help='increase the debug level')
    parser.add_argument('-s', '--setup', required=False,
                        action="store_true", help='run the setup process')
    args = parser.parse_args()

    if args.debug:
        DBG_LEVEL += 1
        print('Debug, increased debug level')

    if args.setup or config_bad < 0:
        if config_bad < -1:
            print('Error, severe problem with settings, please fix and restart program')
            print('%s' % (error_text,) )
            exit(1)
        if config_bad < 0:
            print('%s' % (error_text,) )
        settings_editor(settings_dir, settings_file)
    else:
        clock_app()


##########################################################################################

if __name__ == "__main__":
    DBG_LEVEL = 0
    KEY_STROKE = ''
    QUIT_FLAG = False
    STOP_PLAYBACK = False

    EVENT = Event()
    MY_SETTINGS = configparser.ConfigParser()
    main()

# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
