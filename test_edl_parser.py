import re
import json
import csv

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

from get_all_videos_authenticated import getAllVideosFromChannel

try:
  import asyncio
except ImportError:
  import trollius as asyncio

try:
  import urllib2 as urllib
except ImportError:
  import urllib.request as urllib

# Modification for Python 3
import urllib as urllibparse

class NaiveEDLParserAndPublisher(object):
  def __init__(self, applyEDLAdjustment = True):
    # prepare trollius logging
    self.prepareLogging()

    self._events = dict()
    self._running = False
    self._applyEDLAdjustment = applyEDLAdjustment
    
    # NDN related variables
    self._loop = asyncio.get_event_loop()
    self._face = ThreadsafeFace(self._loop)
    
    # Use the system default key chain and certificate name to sign commands.
    self._keyChain = KeyChain()
    self._keyChain.setFace(self._face)
    self._certificateName = self._keyChain.getDefaultCertificateName()
    self._face.setCommandSigningInfo(self._keyChain, self._certificateName)
    self._memoryContentCache = MemoryContentCache(self._face)
    
    # Publishing parameters conf  iguration
    self._translationServiceUrl = "http://the-archive.la/losangeles/services/get-youtube-url"
    self._namePrefixString = "/ndn/edu/ucla/remap/test/edl/"

    self._dataLifetime = 2000
    self._publishBeforeSeconds = 3
    self._translateBeforeSeconds = 60
    self._currentIdx = 0

    # Youtube related variables: 
    # Channel Global song: UCSMJaKICZKXkpvr7Gj8pPUg
    # Channel Los Angeles: UCeuQoBBzMW6SWkxd8_1I8NQ
    # self._channelID = 'UCSMJaKICZKXkpvr7Gj8pPUg'
    self._channelID = 'UCSMJaKICZKXkpvr7Gj8pPUg'
    self._accessKey = 'AIzaSyCe8t7PnmWjMKZ1gBouhP1zARpqNwHAs0s'
    #queryStr = 'https://www.googleapis.com/youtube/v3/videos?part=snippet,contentDetails,statistics,status&key=' + apiKey + '&id='
    # Video query example
    #https://www.googleapis.com/youtube/v3/videos?part=snippet,contentDetails,statistics,status&key=AIzaSyDUY_AX1iJQcwCW1mASEp5GcLtq1V9BM1Q&id=_ebELPKANxo
    # Channel query example
    #https://www.googleapis.com/youtube/v3/search?key=AIzaSyCe8t7PnmWjMKZ1gBouhP1zARpqNwHAs0s&channelId=UCSMJaKICZKXkpvr7Gj8pPUg&part=snippet,id&order=date&maxResults=20
    self._videoUrlDict = dict()
    
    self._edlAdjustmentDict = dict()
    return
  
  def getClipUrlOAuth(self):
    self._videoUrlDict = dict((k.lower(), v) for k,v in getAllVideosFromChannel().iteritems())
  
  # Old getClipUrl function that looks at the public Youtube channel without using Python API
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
        #  print("* " + item)
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
              "src_url": "%s", \
              "translated": "%s" \
             }' % (str(eventID), reelName, channel, trans, srcStartTime, srcEndTime, dstStartTime, dstEndTime, "none", "none"))
          
          isEventBegin = False
          lastEventID = eventID
        elif (re.match(r'\s+', line) is not None or line == ''):
          isEventBegin = True
        elif lastEventID > 0:
          # Skipping events that do not have right offset
          if not eventID in self._events:
            print('Line skipped because of missing start time adjustment')
            continue

          fromClipNameMatch = re.match(r'\* FROM CLIP NAME: ([^\n]*)\n', line) 
          if (fromClipNameMatch is not None):
            clipName = fromClipNameMatch.group(1).strip()
            parsedClipName = (clipName.lower().replace('_', ' ').replace('-', ' '))

            if self._applyEDLAdjustment:
              if clipName in self._edlAdjustmentDict:
                startTimeAdjusted = self.getTimeMinus(self._edlAdjustmentDict[clipName].split(':'), self._events[eventID]['src_start_time'].split(':'))
                endTimeAdjusted = self.getTimeMinus(self._edlAdjustmentDict[clipName].split(':'), self._events[eventID]['src_end_time'].split(':'))
                self._events[eventID]['src_start_time'] = startTimeAdjusted
                self._events[eventID]['src_end_time'] = endTimeAdjusted

                # Skipping events that do not have right offset
                if startTimeAdjusted == "" or endTimeAdjusted == "":
                  print(clipName + " : " + startTimeAdjusted, " start time incorrect; event " + str(eventID) + " ignored")
                  del self._events[eventID]
                  continue
              else:
                # Skipping events that do not have right offset
                print('Warning: EDL adjustment not found for ' + clipName + "; event " + str(eventID) + " ignored")
                del self._events[eventID]
                continue

            # We don't do audio (only .wav or .mp3) for now
            if parsedClipName.endswith('.wav') or parsedClipName.endswith('.mp3'):
              continue
            else:
              parsedClipName = (" ").join(parsedClipName.split('.')[:-1])
              # print(parsedClipName)

            if parsedClipName in self._videoUrlDict:
              # we assume one src_url from one FROM CLIP NAME for now
              self._events[eventID]['src_url'] = 'https://www.youtube.com/watch?v=' + self._videoUrlDict[parsedClipName]
              print('src_url is '+self._events[eventID]['src_url'])
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

      latestEventTime = 0
      lastEventID = 0
      for event_id in sorted(self._events):
        timeStrs = self._events[event_id]['dst_start_time'].split(':')
        publishingTime = self.getScheduledTime(timeStrs, self._publishBeforeSeconds)
        translationTime = self.getScheduledTime(timeStrs, self._translateBeforeSeconds)
        if publishingTime > latestEventTime:
          latestEventTime = publishingTime
        self._loop.call_later(translationTime, self.translateUrl, event_id)
        self._loop.call_later(publishingTime, self.publishData, event_id)
        lastEventID = event_id

      # append arbitrary 'end' data
      lastEventID = lastEventID + 1
      self._events[lastEventID] = json.loads('{ \
        "event_id": "%s", \
        "src_url": "%s", \
        "translated": "%s" \
      }' % (str(lastEventID), "end", "not-required"))
      startTime = self.getScheduledTime(self._events[lastEventID-1]['src_start_time'].split(':'),0)
      endTime = self.getScheduledTime(self._events[lastEventID-1]['src_end_time'].split(':'),0)
      print('scheduled end '+str(endTime-startTime)+' sec from now')
      self._loop.call_later(latestEventTime + 1, self.publishData, lastEventID)
      self._loop.call_later(latestEventTime + 2 + (endTime-startTime), self._loop.stop)

      self._running = True

  def translateUrl(self, idx):
    queryUrl = self._translationServiceUrl
    timeStrs = self._events[idx]['src_start_time'].split(':')
    
    # we don't have the video from Youtube
    if self._events[idx]['src_url'] == "none":
      print("no video from Youtube")
      # we still publish the data even if src_url is "none", to maintain consecutive sequence numbers
      self._events[idx]['translated'] = "non-existent"
      return

    serviceUrl = self._events[idx]['src_url'] #+ "&t=" + str(self.timeToSeconds(timeStrs)) + "s"

    values = {'url' : serviceUrl,
              'fetchIfNotExist' : 'true' }

    data = urllibparse.urlencode(values)
    req = urllib.Request(queryUrl, data)
    # This synchronous request might block the execution of publishData; should be changed later
    response = urllib.urlopen(req)
    videoUrl = response.read()

    self._events[idx]['ori_url'] = serviceUrl
    self._events[idx]['src_url'] = videoUrl

    if self._events[idx]['translated'] == "publish":
      # We already missed the scheduled publishing time; should publish as soon as translation finishes
      self.publishData(idx)
    else:
      self._events[idx]['translated'] = "translated"
    return

  def publishData(self, idx):
    # Translation of the video URL has finished by the time of the publishData call; 
    # if not, we set translated to "publish"; this is data race free since translateUrl and publishData are scheduled in the same thread
    if self._events[idx]['translated'] != "none":
      # Order published events sequence numbers by start times in destination
      data = Data(Name(self._namePrefixString + str(self._currentIdx)))

      data.setContent(json.dumps(self._events[idx]))
      data.getMetaInfo().setFreshnessPeriod(self._dataLifetime)
      self._keyChain.sign(data, self._certificateName)
      self._memoryContentCache.add(data)
      self._currentIdx += 1
      if __debug__:
        print('Added ' + data.getName().toUri())
    else:
      self._events[idx]['translated'] = "publish"

  def timeToSeconds(self, timeStrs):
    seconds = int(timeStrs[2])
    minutes = int(timeStrs[1])
    hours = int(timeStrs[0])
    ret = hours * 3600 + minutes * 60 + seconds
    return ret

  def getTimeMinus(self, timeStrs1, timeStrs2):
    frameNumber = int(timeStrs1[3])
    seconds = int(timeStrs1[2])
    minutes = int(timeStrs1[1])
    hours = int(timeStrs1[0])

    frameNumber2 = int(timeStrs2[3]) - frameNumber
    seconds2 = int(timeStrs2[2]) - seconds
    minutes2 = int(timeStrs2[1]) - minutes
    hours2 = int(timeStrs2[0]) - hours

    if frameNumber2 < 0:
      # frame rate assumption
      frameNumber2 = 30 + frameNumber2
      seconds2 = seconds2 - 1

    if seconds2 < 0:
      seconds2 = 60 + seconds2
      minutes2 = minutes2 - 1

    if minutes2 < 0:
      minutes2 = 60 + minutes2
      hours2 = hours2 - 1

    if hours2 < 0:
      print("Warning: time minus smaller than 0")
      return ""
    
    # Arbitrary guard of start times that are off
    if hours2 > 1 or minutes2 > 1:
      return ""

    return ":".join([str(hours2), str(minutes2), str(seconds2), str(frameNumber2)]) 

  def getScheduledTime(self, timeStrs, beforeSeconds):
    frameNumber = int(timeStrs[3])
    seconds = int(timeStrs[2])
    minutes = int(timeStrs[1])
    hours = int(timeStrs[0])
    ret = hours * 3600 + minutes * 60 + seconds - beforeSeconds
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

  ############################
  def loadEDLAdjustment(self, csvFile):
    with open(csvFile, "rb") as csvfile:
      reader = csv.reader(csvfile, delimiter=',', quotechar='|')
      for row in reader:
        self._edlAdjustmentDict[row[3]] = row[1]

if __name__ == '__main__':
  naiveEDLParser = NaiveEDLParserAndPublisher()
# <<<<<<< Updated upstream
  if naiveEDLParser._applyEDLAdjustment:
    naiveEDLParser.loadEDLAdjustment('rough-cut.csv')
  naiveEDLParser.getClipUrlOAuth()
  naiveEDLParser.parse('ROUGH CUT_v02_ZS.edl')
# =======
  # if 0:
  #   naiveEDLParser.getClipUrlOAuth()
  #   naiveEDLParser.parse('ROUGH CUT_v02_ZS.edl')
  # else:
  #   naiveEDLParser.getClipUrl()
  #   naiveEDLParser.parse('sequence-0-1.edl')
# >>>>>>> Stashed changes
  naiveEDLParser._loop.run_until_complete(naiveEDLParser.startPublishing())

  naiveEDLParser._loop.run_forever()
