# -*- coding: utf-8 -*-

import json
import requests
import types
import urllib
import re
from functools import lru_cache

try:
    from PySide2 import QtCore
    from PySide2 import QtWidgets
except:
    from PyQt5 import QtCore
    from PyQt5 import QtWidgets

import ItemProperty as iprop

import logging
logger = logging.getLogger("PySel.SyncthingAPI")

# the following url was used to build API
# https://www.digitalocean.com/community/tutorials/how-to-use-web-apis-in-python-3

class SyncthingAPI:
    def __init__(self, parent):
        self.api_version = 0
        self.api_token = None
        self.api_protocol = "http"
        self.api_port = 8384
        self.api_hostname = "localhost"
        self.headerSelectStart = '//* Selective sync (generated by pyselective) *//'
        self.headerSelectFinish = '//* ignore all except selected *//'
        self._ignoreSelectiveList = []
        self._parent = parent
        # try set date format
        try:
            self.df = QtCore.Qt.ISODateWithMs
        except AttributeError:
            self.df = QtCore.Qt.ISODate
            logger.warning("Your Qt version is too old, date conversion could be incomplete")


    def startSession(self):
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers = {'X-API-Key': self.api_token}

    @property
    def api_url_base(self):
        return f"{self.api_protocol}://{self.api_hostname}:{self.api_port}/rest/"

    @api_url_base.setter
    def api_url_base(self, url):
        p = '(?P<scheme>http[^:]?)?(?:\://)?(?P<host>[^:/ ]+).?(?P<port>[0-9]*).*'
        m = re.search(p, url)
        self.api_hostname = m.group('host')
        s = m.group('scheme')
        if s:
            self.api_protocol = s
        p = m.group('port')
        if p:
            self.api_port = p

    def _getRequest(self, suff):
        api_url = self.api_url_base + suff
        response = self.session.get(api_url)

        if isinstance(response, types.GeneratorType):
            raise ImportError('It seems you use \"yieldfrom.request\" instead of \"requests\"')

        if response.status_code == 200:
            # logger.debug("Response content: {}".format(response.content))
            return json.loads(response.content.decode('utf-8'))
        elif response.status_code == 403:
            raise requests.RequestException('Forbidden, api token can be wrong')
        elif response.status_code == 404:
            logger.info("No object in the index: {0}".format(suff))
            return {}
        elif response.status_code == 500:
            logger.info("Internal Server Error: {0} - {1}".format(
                            suff, response.content.decode('utf-8')))
            raise requests.RequestException("Internal Server Error: {0} - {1}".format(
                            suff, response.content.decode('utf-8')))
        else:
            raise requests.RequestException('Wrong status code: '+ str(response.status_code) + " (" + suff + ")")

    def _postRequest(self, suff, d):
        api_url = self.api_url_base + suff
        self.session.post(api_url,  json = d)

    def _refineBrowseFolderRequest(self, d, rv = None):
        # to avoid copying
        if rv is None:
            rv = []

        if self.api_version >= self.verStr2Num("1.14.0"):
            return d

        # refine dict with list to list of dicts
        # if the version is lower than 1.14.0
        for key in d:
            if isinstance(d[key], dict):
                rv.append({ 'name' : key, 'type': 'FILE_INFO_TYPE_DIRECTORY', 'children': [] })
                self._refineBrowseFolderRequest(d[key], rv[-1]['children'])
            else:
                rv.append({ 'name' : key, 'type': 'FILE_INFO_TYPE_FILE'})
        return rv

    def getFolderIter(self):
        return self._getRequest('stats/folder').keys()

    def getFoldersDict(self):
        dicts = self._getRequest('stats/folder')
        cfgd = self._getRequest('system/config')
        for k in dicts.keys():
            for f in cfgd['folders']:
                if f['id'] == k:
                    dicts[k]['label'] = f['label']
                    dicts[k]['path'] = f['path']
        return dicts

    @lru_cache(maxsize=100)
    def getIgnoreList(self, fid):
        rv = self._getRequest('db/ignores?folder={0}'.format(fid))['ignore']
        logger.debug("Ignore list: {}".format(rv))
        if rv is None:
            return []
        return rv

    def getIgnoreSelective(self, fid):
        l = self.getIgnoreList(fid)
        if l.count(self.headerSelectStart) == 0 or \
                l.count(self.headerSelectFinish) == 0:
            return []
        indstart = l.index(self.headerSelectStart)
        indend = l.index(self.headerSelectFinish)
        return l[indstart+1:indend]

    def setIgnoreSelective(self, fid, il):
        l = self.getIgnoreList(fid)
        logger.debug(l)
        indstart = l.index(self.headerSelectStart)
        indend = l.index(self.headerSelectFinish)

        if len(il) > 1 and il[-1].strip() == '':
            il[-1] = '\n'
        else:
            il.append('\n')

        sendlist = l[:indstart+1] + il + l[indend:]
        self._postRequest('db/ignores?folder={0}'.format(fid), {'ignore': sendlist})

    def browseFolder(self, fid):
        d = self._getRequest('db/browse?folder={0}'.format(fid))
        self._ignoreSelectiveList = self.getIgnoreSelective(fid)
        return self._refineBrowseFolderRequest(d)

    def browseFolderPartial(self, fid, path='', lev=0):
        if path == '':
            d = self._getRequest('db/browse?folder={0}&levels={1}'.format(fid, lev))
        else:
            d = self._getRequest('db/browse?folder={0}&prefix={1}&levels={2}'.format(fid, path, lev))
        self._ignoreSelectiveList = self.getIgnoreSelective(fid) # TODO caching
        return self._refineBrowseFolderRequest(d)

    @lru_cache(maxsize=100)
    def getFileInfoExtended(self, fid, fn):
        'fn: file name with path relative to the parent folder'
        rv = self._getRequest('db/file?folder={0}&file={1}'.format(fid, urllib.parse.quote(fn)))
        if len(rv) > 0 and (iprop.Type[rv['local']['type']] is iprop.Type.DIRECTORY or rv['local']['type'] == 1):
            if ("!/" + fn) in self._ignoreSelectiveList:
                rv['local']['ignored'] = False
                if ("/" + fn + "/**") in self._ignoreSelectiveList:
                    ispartial = True
                else:
                    ispartial = False
            else: # assume ignored by default as it is not in the list
                rv['local']['ignored'] = True
                ispartial = False

            for ign in self._ignoreSelectiveList:
                if ign.startswith("!/" + fn + "/"):
                    ispartial = True
                    # there is some content inside, so it can not be ignored
                    rv['local']['ignored'] = False
                    break
                elif ("!/" + fn + "/").startswith(ign + "/") and \
                        (ign[1:] + "/**") not in self._ignoreSelectiveList:
                    # the parent is on the SelectiveList, so the item must be fully synced
                    rv['local']['ignored'] = False
                    ispartial = False
            rv['local']['partial'] = ispartial
        return rv

    def getFileInfoRaw(self, fid, fn):
        'fn: file name with path relative to the parent folder'
        return self._getRequest('db/file?folder={0}&file={1}'.format(fid, urllib.parse.quote(fn)))

    def getVersion(self):
        logger.debug("Try read syncthing version...")
        rv = self._getRequest('svc/report')['version']
        self.api_version = self.verStr2Num(rv)
        logger.debug("Ok: {0} (api {1})".format(rv, self.api_version))
        return rv

    def verStr2Num(self, s):
        l = s.replace("v", "").split(".")
        return (int(l[0])*100 + int(l[1]))*100 + int(l[2])

    def clearCache(self):
        self.getIgnoreList.cache_clear()
        self.getFileInfoExtended.cache_clear()

    def extendFileInfo(self, fid, l, path = '', psyncstate=iprop.SyncState.unknown):
        try:
            contents = self.browseFolderPartial(fid, path, lev=1)
        except requests.exceptions.RequestException as e:
            logger.warning("extendFileInfo failed with lev=1 (fid={}, path={}), try lev=0 recursively".format(fid, path))
            logger.debug("Exception {}".format(e))
            # try to force read of the missed folder
            name = str(e).split('could not find child')[1].split('\'')[1]
            fldi = self.getFileInfoExtended( fid,
                    name if not path else path + '/' + name)
            faileditem = {'name':name, 'modTime': fldi['global']['modified'],
                    'size': fldi['global']['size'], 'type': fldi['global']['type']}
            l.append(faileditem)
            contents = self.browseFolderPartial(fid, path, lev=0) + [faileditem]
            for cind in range(len(contents)):
                if iprop.Type[contents[cind]['type']] is iprop.Type.DIRECTORY:
                    contents[cind]['children'] = self.browseFolderPartial(fid,
                                    contents[cind]['name'] if not path else path + '/' + contents[cind]['name'], lev=0)
            QtWidgets.QMessageBox.warning(self._parent,
                    "Database read error",
                    "Force to read path \'{}\', but some other folders may be missed due to database inconsistency".format(name if not path else path + '/' + name) +
                    "\n\n Additional info:\n" +
                    "availability: {}\n".format(fldi['availability']) +
                    "modifiedBy: {}".format(fldi['global']['modifiedBy'])
                )

        if path != '' and path[-1] != '/':
            path = path + '/'
        for v in l:
            extd = self.getFileInfoExtended( fid, path+v['name'])
            if len(extd) == 0:  # there is no such file in database
                continue
            v['size'] = extd['global']['size']
            v['modified'] = QtCore.QDateTime.fromString( extd['global']['modified'], self.df)
            v['ignored'] = extd['local']['ignored']
            v['invalid'] = extd['local']['invalid']

            if iprop.Type[v['type']] is iprop.Type.DIRECTORY:
                # TODO dict of dicts to avoid for
                for c in contents:
                    if c['name'] == v['name']:
                        if 'children' in c:
                            v['children'] = c['children']
                        else:
                            v['children'] = []

            if iprop.Type[v['type']] is not iprop.Type.DIRECTORY:
                pass
            elif 'partial' in extd['local']:
                v['partial'] = extd['local']['partial']
            # seems the following case do not work at all as 'partial' exists forever
            else: #do not believe 'ignore', check content
                selcnt = 0
                for v2 in v['children']:
                    if self.getFileInfoExtended( \
                            fid, path+v['name']+'/' + v2['name'])['local']['ignored'] == False:
                        selcnt += 1

                if selcnt == 0:
                    v['partial'] = False
                elif selcnt == len(v['children']):
                    v['ignored'] = False
                    v['partial'] = False
                else:
                    v['ignored'] = False
                    v['partial'] = True

            if 'partial' in v and v['partial']:
                v['syncstate'] = iprop.SyncState.partial
            elif not v['ignored']:
                v['syncstate'] = iprop.SyncState.syncing
            elif psyncstate == iprop.SyncState.syncing:
                # item ignored but the parent does not
                # so it must be in global ignore patterns
                v['syncstate'] = iprop.SyncState.globalignore
            else:
                v['syncstate'] = iprop.SyncState.ignored

    def extendDirSizes(self, l):
        '''Return the size of the current folder'''
        #logger.debug("extendDirSizes: {}".format(l))
        size = 0
        completed = True
        for item in l:
            logger.debug("extend Item Size: {}".format(item))
            if not 'type' in item.keys():
                completed = False
                continue
            if iprop.Type[item['type']] is iprop.Type.DIRECTORY:
                if 'syncstate' in item.keys() and item['syncstate'] is iprop.SyncState.newlocal:  # let's skip local files for now
                    continue
                item['size'] = 0  # clear inode size
                if 'children' in item.keys():
                    item['extSize'] = self.extendDirSizes(item['children'])
                    logger.debug("extend size={} for {}".format(item['extSize'], item))
                else:
                    completed = False
            if 'size' in item.keys():
                if 'extSize' in item.keys():  # true for a folder
                    size += item['extSize']['value']
                    completed &= item['extSize']['completed'] if 'completed' in item['extSize'].keys() else True
                else:
                    size += item['size']
            else:
                completed = False
        return {"value":size, "completed":completed}
