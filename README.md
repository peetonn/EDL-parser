EDL parser and NDN publisher

Temporary namespace:

```
/test/edl/<seq-no>
```

Where seq-no is ordered by dst\_start\_time on the publisher.

And payload format:

```javascript
{
	"event_id": "3", 
	"reel_name": "AX", 
	"src_end_time": "00:01:07:12", 
	"src_url": "https://www.youtube.com/watch?v=0i1MCE8P0Gg", 
	"dst_end_time": "00:00:14:12", 
	"dst_start_time": "00:00:06:18", 
	"trans": "C",
	"channel": "V",
	"src_start_time": "00:00:59:20"
}
```

The last packet has the special format of:

```javascript
{
	"event_id": <number_of_event> + 1, 
	"src_url": "end"
}
```

Dependency:
* PyNDN
* (If using OAuth fetching) Google Python API: pip install --upgrade google-api-python-client
* (If using OAuth fetching) oauth2client: pip install --upgrade oauth2client==1.3.2

Zhehao <zhehao@remap.ucla.edu>