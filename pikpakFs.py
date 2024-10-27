import httpx
from hashlib import md5
from pikpakapi import PikPakApi
from typing import Dict
from datetime import datetime
import json
import re
import os
import logging

class PathWalker():
    def __init__(self, pathStr : str, sep : str = "/"):
        self.__pathSpots : list[str] = []
        if not pathStr.startswith(sep):
            self.__pathSpots.append(".")
        pathSpots = pathStr.split(sep)
        self.__pathSpots.extend(pathSpots)
    
    def IsAbsolute(self) -> bool:
        return len(self.__pathSpots) == 0 or self.__pathSpots[0] != "."

    def AppendSpot(self, spot):
        self.__pathSpots.append(spot)
    
    def Walk(self) -> list[str]:
        return self.__pathSpots

class VirtFsNode:
    def __init__(self, id : str, name : str, fatherId : str):
        self.id = id
        self.name = name
        self.fatherId = fatherId
        self.lastUpdate : datetime = None

class DirNode(VirtFsNode):
    def __init__(self, id : str, name : str, fatherId : str):
        super().__init__(id, name, fatherId)
        self.childrenId : list[str] = []
        
class FileNode(VirtFsNode):
    def __init__(self, id : str, name : str, fatherId : str):
        super().__init__(id, name, fatherId)
        self.url : str = None

class PikpakToken:
    def __init__(self, username, password, access_token, refresh_token, user_id):
        self.username = username
        self.password = password
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.user_id = user_id

    def to_json(self):
        return json.dumps(self.__dict__)

    @classmethod
    def from_json(cls, json_str):
        data = json.loads(json_str)
        return cls(**data)

class PKVirtFs:
    def __init__(self, loginCachePath : str = None, proxy : str = None):
        self.nodes : Dict[str, VirtFsNode] = {} 
        self.root = DirNode(None, "", None)
        self.currentLocation = self.root

        self.loginCachePath = loginCachePath
        self.proxyConfig = proxy
        self.client : PikPakApi = None
        self.__TryLoginFromCache()

    def __InitClientByToken(self, token : PikpakToken):
        self.__InitClientByUsernamePassword(token.username, token.password)
        self.client.access_token = token.access_token
        self.client.refresh_token = token.refresh_token
        self.client.user_id = token.user_id
        self.client.encode_token()

    def __InitClientByUsernamePassword(self, username : str, password : str):
        httpx_client_args = None
        if self.proxyConfig != None:
            httpx_client_args = {
                "proxy": self.proxyConfig,
                "transport": httpx.AsyncHTTPTransport()
            }

        self.client = PikPakApi(
            username = username,
            password = password,
            httpx_client_args=httpx_client_args)

    def __TryLoginFromCache(self):
        if self.loginCachePath is None:
            return
        if not os.path.exists(self.loginCachePath):
            return
        with open(self.loginCachePath, 'r', encoding='utf-8') as file:
            content = file.read()
            token = PikpakToken.from_json(content)
            self.__InitClientByToken(token)
            logging.info("successfully load login info from cache") 
    
    def __DumpLoginInfo(self):
        if self.loginCachePath is None:
            return
        with open(self.loginCachePath, 'w', encoding='utf-8') as file:
            token = PikpakToken(self.client.username, self.client.password, self.client.access_token, self.client.refresh_token, self.client.user_id)
            file.write(token.to_json())
            logging.info("successfully dump login info to cache")
    
    def __IsAncestorsOf(self, nodeA : VirtFsNode, nodeB : VirtFsNode) -> bool:
        if nodeB is nodeA:
            return False
        if nodeA is self.root:
            return True
        while nodeB.fatherId != None:
            nodeB = self.nodes[nodeB.fatherId]
            if nodeB is nodeA:
                return True
        return False

    def ToDir(self, node : VirtFsNode) -> DirNode:
        if isinstance(node, DirNode):
            return node
        return None
    
    def ToFile(self, node : VirtFsNode) -> FileNode:
        if isinstance(node, FileNode):
            return node
        return None

    def GetFatherNode(self, node : VirtFsNode) -> VirtFsNode:
        if node.fatherId is None:
            return self.root
        return self.nodes[node.fatherId]

    def FindChildInDirByName(self, dir : DirNode, name : str):
        if dir is self.root and name == "":
            return self.root
        for childId in dir.childrenId:
            node = self.nodes[childId]
            if name == node.name:
                return node
        return None

    async def RefreshDirectory(self, dirNode : DirNode):
        next_page_token = None
        nodes = []
        while True:
            dirInfo = await self.client.file_list(parent_id = dirNode.id, next_page_token=next_page_token, size=3)
            next_page_token = dirInfo["next_page_token"]
            currentPageNodes = dirInfo["files"]
            nodes.extend(currentPageNodes)
            if next_page_token is None or next_page_token == "":
                break
        dirNode.childrenId.clear()

        for node in nodes:
            child : VirtFsNode = None
            id = node["id"]
            name = node["name"]
            fatherId = dirNode.id
            if id in self.nodes:
                child = self.nodes[id]
            else:
                child = DirNode(id, name, fatherId) if node["kind"].endswith("folder") else FileNode(id, name, fatherId)
                self.nodes[id] = child
            child.name = name
            child.fatherId = fatherId
            dirNode.childrenId.append(id)
        dirNode.lastUpdate = datetime.now()

    async def PathToNode(self, pathStr : str) -> VirtFsNode:
        father, sonName = await self.PathToFatherNodeAndNodeName(pathStr)
        if sonName == "":
            return father
        fatherDir = self.ToDir(father)
        if fatherDir is None:
            return None
        return self.FindChildInDirByName(father, sonName)
      
    async def PathToFatherNodeAndNodeName(self, pathStr : str) -> tuple[VirtFsNode, str]:
        pathWalker = PathWalker(pathStr)
        father : VirtFsNode = None
        sonName : str = None
        current = self.root if pathWalker.IsAbsolute() else self.currentLocation
        
        for spot in pathWalker.Walk():
            if current is None:
                father = None
                break
            if spot == "..":
                current = self.GetFatherNode(current)
                continue
            father = current
            currentDir = self.ToDir(current)
            if currentDir is None:
                current = None
                continue
            if currentDir.lastUpdate is None:
                await self.RefreshDirectory(currentDir)
            if spot == ".":
                continue
            sonName = spot
            current = self.FindChildInDirByName(currentDir, spot)
            
        if current != None:
            currentDir = self.ToDir(current)
            if currentDir != None and currentDir.lastUpdate is None:
                await self.RefreshDirectory(currentDir)
            father = self.GetFatherNode(current)
            sonName = current.name
        
        return father, sonName

    def NodeToPath(self, node : VirtFsNode) -> str:
        if node is self.root:
            return "/"
        spots : list[str] = []
        current = node
        while current.id != None:
            spots.append(current.name)
            if current.fatherId is None:
                break
            current = self.nodes[current.fatherId]
        spots.append("")
        return "/".join(reversed(spots))

    async def MakeDir(self, node : DirNode, name : str) -> DirNode:
        await self.client.create_folder(name, node.id)
        await self.RefreshDirectory(node)
        return self.ToDir(self.FindChildInDirByName(node, name))

    async def Login(self, username : str = None, password : str = None) -> None:
        if self.client != None and username is None and password is None:
            username = self.client.username
            password = self.client.password

        if username == None and password == None:
            raise Exception("Username and password are required")
        
        self.__InitClientByUsernamePassword(username, password)
        await self.client.login()
        self.__DumpLoginInfo()
    
    async def UpdateDownloadUrl(self, file : FileNode) -> None:
        result = await self.client.get_download_url(file.id)
        file.url = result["web_content_link"]

    async def Download(self, url : str, dirNode : DirNode) -> None :
        # 默认创建在当前目录下
        # todo: 完善离线下载task相关
        if dirNode is None:
            dirNode = self.currentLocation
        await self.client.offline_download(url, dirNode.id)

    async def Delete(self, node : VirtFsNode) -> None:
        father = self.GetFatherNode(node)
        fatherDir = self.ToDir(father)
        if fatherDir is None:
            raise Exception('Failed to locate')
        if self.currentLocation is node or self.__IsAncestorsOf(node, self.currentLocation):
            raise Exception('Delete self or ancestor is not allowed')
        
        await self.client.delete_to_trash([node.id])
        await self.RefreshDirectory(fatherDir)