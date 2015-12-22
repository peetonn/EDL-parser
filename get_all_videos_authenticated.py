#!/usr/bin/python
# Code taken from Google's Python API example of getting videos in my channel

import httplib2
import os
import sys

from apiclient.discovery import build
from oauth2client.client import flow_from_clientsecrets
from oauth2client.file import Storage
from oauth2client.tools import argparser, run_flow


# The CLIENT_SECRETS_FILE variable specifies the name of a file that contains
# the OAuth 2.0 information for this application, including its client_id and
# client_secret. You can acquire an OAuth 2.0 client ID and client secret from
# the Google Developers Console at
# https://console.developers.google.com/.
# Please ensure that you have enabled the YouTube Data API for your project.
# For more information about using OAuth2 to access the YouTube Data API, see:
#   https://developers.google.com/youtube/v3/guides/authentication
# For more information about the client_secrets.json file format, see:
#   https://developers.google.com/api-client-library/python/guide/aaa_client_secrets
CLIENT_SECRETS_FILE = "client_secrets.json"

# This variable defines a message to display if the CLIENT_SECRETS_FILE is
# missing.
MISSING_CLIENT_SECRETS_MESSAGE = """
WARNING: Please configure OAuth 2.0

To make this sample run you will need to populate the client_secrets.json file
found at:

   %s

with information from the Developers Console
https://console.developers.google.com/

For more information about the client_secrets.json file format, please visit:
https://developers.google.com/api-client-library/python/guide/aaa_client_secrets
""" % os.path.abspath(os.path.join(os.path.dirname(__file__),
                                   CLIENT_SECRETS_FILE))

# This OAuth 2.0 access scope allows for read-only access to the authenticated
# user's account, but not other types of account access.
YOUTUBE_READONLY_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"

# For testing if the OAuth redirect url is set up correctly:
#  https://accounts.google.com/AccountChooser?continue=https%3A%2F%2Faccounts.google.com%2Fo%2Foauth2%2Fauth%3Faccess_type%3Doffline%26scope%3Dhttps%3A%2F%2Fwww.googleapis.com%2Fauth%2Fanalytics.readonly%26response_type%3Dcode%26redirect_uri%3Dhttp%3A%2F%2Flocalhost%3A8080%2F%26client_id%3D402991078443-m4viuofsqb1s61e05bbvi8ejbja46fvj.apps.googleusercontent.com%26hl%3Dzh-CN%26from_login%3D1%26as%3D-2510b106115fceb7&btmpl=authsub&hl=zh_CN

# Realistically, we need to load the videos from other guys channels (that we manage, meaning we need a *-oauth2.json for each of their channel),
# and match the descriptions of their videos with the descriptions of clips in our EDL; (essentially, the "from clip name" field implies a set of tags/descriptions, instead of a specific video name)
def getAllVideosFromChannel():
  flow = flow_from_clientsecrets(CLIENT_SECRETS_FILE,
    message=MISSING_CLIENT_SECRETS_MESSAGE,
    scope=YOUTUBE_READONLY_SCOPE)

  storage = Storage("%s-oauth2.json" % sys.argv[0])
  credentials = storage.get()

  if credentials is None or credentials.invalid:
    flags = argparser.parse_args()
    credentials = run_flow(flow, storage, flags)

  youtube = build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION,
    http=credentials.authorize(httplib2.Http()))

  # Retrieve the contentDetails part of the channel resource for the
  # authenticated user's channel.
  channels_response = youtube.channels().list(
    mine=True,
    part="contentDetails"
  ).execute()

  for channel in channels_response["items"]:
    # From the API response, extract the playlist ID that identifies the list
    # of videos uploaded to the authenticated user's channel.
    uploads_list_id = channel["contentDetails"]["relatedPlaylists"]["uploads"]

    if __debug__:
      print "Videos in list %s" % uploads_list_id

    # Retrieve the list of videos uploaded to the authenticated user's channel.
    playlistitems_list_request = youtube.playlistItems().list(
      playlistId=uploads_list_id,
      part="snippet",
      maxResults=50
    )
    
    result = dict()

    while playlistitems_list_request:
      playlistitems_list_response = playlistitems_list_request.execute()

      # Print information about each video.
      for playlist_item in playlistitems_list_response["items"]:
        title = playlist_item["snippet"]["title"]
        video_id = playlist_item["snippet"]["resourceId"]["videoId"]
        if __debug__:
          print "%s (%s)" % (title, video_id)
        result[title] = video_id

      playlistitems_list_request = youtube.playlistItems().list_next(
        playlistitems_list_request, playlistitems_list_response)

    if __debug__:
      print
    return result

if __name__ == '__main__':
  print(getAllVideosFromChannel())
