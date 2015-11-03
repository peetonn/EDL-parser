import re
import json

import time
import getopt
import sys
import logging
import random

from pyndn import Name, Data, Interest, Exclude, KeyLocator
from pyndn.threadsafe_face import ThreadsafeFace

from pyndn.security import KeyChain
from pyndn.security.identity.file_private_key_storage import FilePrivateKeyStorage
from pyndn.security.identity.basic_identity_storage import BasicIdentityStorage
from pyndn.security.identity.identity_manager import IdentityManager
from pyndn.security.policy.config_policy_manager import ConfigPolicyManager

from pyndn.util.common import Common
from pyndn.util import MemoryContentCache, Blob

try:
  import asyncio
except ImportError:
  import trollius as asyncio

try:
  import urllib2 as urllib
except ImportError:
  import urllib.request as urllib

class NaiveEDLParserAndPublisher(object):
  def __init__(self):
    # prepare trollius logging
    self.prepareLogging()

    self._events = dict()
    self._running = False
    
    # NDN related variables
    self._loop = asyncio.get_event_loop()
    self._face = ThreadsafeFace(self._loop)
    
    # Use the system default key chain and certificate name to sign commands.
    self._keyChain = KeyChain()
    self._keyChain.setFace(self._face)
    self._certificateName = self._keyChain.getDefaultCertificateName()
    self._face.setCommandSigningInfo(self._keyChain, self._certificateName)
    self._memoryContentCache = MemoryContentCache(self._face)
    
    # Publishing parameters configuration
    self._namePrefixString = "/test/edl/"
    self._dataLifetime = 50000
    self._publishBeforeSeconds = 3
    self._currentIdx = 0

    # Youtube related variables: Channel Global Song
    self._channelID = 'UCSMJaKICZKXkpvr7Gj8pPUg'
    self._accessKey = 'AIzaSyCe8t7PnmWjMKZ1gBouhP1zARpqNwHAs0s'
    #queryStr = 'https://www.googleapis.com/youtube/v3/videos?part=snippet,contentDetails,statistics,status&key=' + apiKey + '&id='
    # Video query example
    #https://www.googleapis.com/youtube/v3/videos?part=snippet,contentDetails,statistics,status&key=AIzaSyDUY_AX1iJQcwCW1mASEp5GcLtq1V9BM1Q&id=_ebELPKANxo
    # Channel query example
    #https://www.googleapis.com/youtube/v3/search?key=AIzaSyCe8t7PnmWjMKZ1gBouhP1zARpqNwHAs0s&channelId=UCSMJaKICZKXkpvr7Gj8pPUg&part=snippet,id&order=date&maxResults=20
    self._videoUrlDict = dict()
    return
  
  def getClipUrl(self, nextPageToken = None):
    options = {
      'part': 'snippet,id',
      'order': 'date',
      'maxResults': '20'
    }
    if nextPageToken is not None:
      options['pageToken'] = nextPageToken
    prefix = 'https://www.googleapis.com/youtube/v3/search?'

    queryUrl = prefix + 'key=' + self._accessKey + '&channelId=' + self._channelID
    for item in options:
      queryUrl += '&' + item + '=' + options[item]
    result = json.loads(urllib.urlopen(queryUrl).read())
    for item in result['items']:
      if 'snippet' in item and 'id' in item and 'videoId' in item['id']:
        self._videoUrlDict[item['snippet']['title'].lower()] = item['id']['videoId']
      else:
        print("Unexpected JSON from youtube channel query")
    if ('nextPageToken' in result):
      self.getClipUrl(result['nextPageToken'])
    else:
      if __debug__:
        print("Building videoUrl dict finished; number of entries: " + str(len(self._videoUrlDict)))
        #for item in self._videoUrlDict:
        #  print(item)
    return

  def parse(self, fileName):
    isEventBegin = False
    lastEventID = -1
    with open(fileName, 'r') as edlFile:
      for line in edlFile:
        if isEventBegin:
          components = line.split()
          try:
            eventID = int(components[0])
          except ValueError:
            print("Cannot cast " + components[0] + " to eventID")
            continue
          # We seem to have a fixed number of components here; 
          # reference: http://www.edlmax.com/maxguide.html
          reelName = components[1]
          channel = components[2]
          trans = components[3]

          timeComponentsIdx = len(components) - 4
          
          srcStartTime = components[timeComponentsIdx]
          srcEndTime = components[timeComponentsIdx + 1]
          dstStartTime = components[timeComponentsIdx + 2]
          dstEndTime = components[timeComponentsIdx + 3]

          self._events[eventID] = json.loads('{ \
              "event_id": "%s", \
              "reel_name": "%s", \
              "channel": "%s", \
              "trans": "%s", \
              "src_start_time": "%s", \
              "src_end_time": "%s", \
              "dst_start_time": "%s", \
              "dst_end_time": "%s", \
              "src_url": "%s" \
             }' % (str(eventID), reelName, channel, trans, srcStartTime, srcEndTime, dstStartTime, dstEndTime, "none"))
          
          isEventBegin = False
          lastEventID = eventID
        elif (re.match(r'\s+', line) is not None or line == ''):
          isEventBegin = True
        elif lastEventID > 0:
          fromClipNameMatch = re.match(r'\* FROM CLIP NAME: ([^\n]*)\n', line) 
          if (fromClipNameMatch is not None):
            clipName = fromClipNameMatch.group(1)
            parsedClipName = (clipName.lower().replace('_', ' ').replace('-', ' '))
            # We don't do audio (only .wav) for now
            if parsedClipName.endswith('.wav'):
              continue
            else:
              parsedClipName = parsedClipName.split('.')[0]
            if parsedClipName in self._videoUrlDict:
              # we assume one src_url from one FROM CLIP NAME for now
              self._events[eventID]['src_url'] = 'https://www.youtube.com/watch?v=' + self._videoUrlDict[parsedClipName]
            else:
              print('Warning: file not found in Youtube channel: ' + clipName)
          else:  
            if ('payload' not in self._events[eventID]):
              self._events[eventID]['payload'] = [line]
            else:
              self._events[eventID]['payload'].append(line)
  
  @asyncio.coroutine
  def startPublishing(self):
    if (len(self._events) == 0):
      return
    elif (not self._running):
      self._memoryContentCache.registerPrefix(Name(self._namePrefixString), self.onRegisterFailed, self.onDataNotFound)
      startTime = time.time()

      for event_id in sorted(self._events):
        timeStrs = self._events[event_id]['dst_start_time'].split(':')
        remainingTime = self.getScheduledTime(timeStrs)
        self._loop.call_later(remainingTime, self.publishData, event_id)

      self._running = True

  def publishData(self, idx):
    # Order published events sequence numbers by start times in destination
    data = Data(Name(self._namePrefixString + str(self._currentIdx)))
    data.setContent(json.dumps(self._events[idx]))
    data.getMetaInfo().setFreshnessPeriod(self._dataLifetime)
    self._keyChain.sign(data, self._certificateName)
    self._memoryContentCache.add(data)
    self._currentIdx += 1
    if __debug__:
      print('Added ' + data.getName().toUri())

  def getScheduledTime(self, timeStrs):
    frameNumber = int(timeStrs[3])
    seconds = int(timeStrs[2])
    minutes = int(timeStrs[1])
    hours = int(timeStrs[0])
    ret = hours * 3600 + minutes * 60 + seconds - self._publishBeforeSeconds
    return (0 if ret < 0 else ret)

  def onRegisterFailed(self, prefix):
    raise RuntimeError("Register failed for prefix", prefix.toUri())
  
  def onDataNotFound(self, prefix, interest, face, interestFilterId, filter):
    print('Data not found for interest: ' + interest.getName().toUri())
    return

#############################
# Logging
#############################
  def prepareLogging(self):
      self.log = logging.getLogger(str(self.__class__))
      self.log.setLevel(logging.DEBUG)
      logFormat = "%(asctime)-15s %(name)-20s %(funcName)-20s (%(levelname)-8s):\n\t%(message)s"
      self._console = logging.StreamHandler()
      self._console.setFormatter(logging.Formatter(logFormat))
      self._console.setLevel(logging.INFO)
      # without this, a lot of ThreadsafeFace errors get swallowed up
      logging.getLogger("trollius").addHandler(self._console)
      self.log.addHandler(self._console)

  def setLogLevel(self, level):
      """
      Set the log level that will be output to standard error
      :param level: A log level constant defined in the logging module (e.g. logging.INFO) 
      """
      self._console.setLevel(level)

  def getLogger(self):
      """
      :return: The logger associated with this node
      :rtype: logging.Logger
      """
      return self.log


if __name__ == '__main__':

  naiveEDLParser = NaiveEDLParserAndPublisher()
  naiveEDLParser.getClipUrl()
  naiveEDLParser.parse('sequence-0-1.edl')
  naiveEDLParser._loop.run_until_complete(naiveEDLParser.startPublishing())

  naiveEDLParser._loop.run_forever()
