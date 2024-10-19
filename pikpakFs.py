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
    def __init__(self, pathStr : str, subDir : str = None, sep : str = "/"):
        self.pathSpots : list[str] = []
        pathStr = pathStr.strip()
        if not pathStr.startswith(sep):
            self.pathSpots.append(".")
        pathSpots = [spot.strip() for spot in pathStr.split(sep) if spot.strip() != ""]
        self.pathSpots.extend(pathSpots)
        if subDir != None:
            self.pathSpots.append(subDir)
    
    def IsAbsolute(self) -> bool:
        return len(self.pathSpots) == 0 or self.pathSpots[0] != "."

class VirtFsNode:
    def __init__(self, id : str, name : str, fatherId : str):
        self.id = id
        self.name = name
        self.fatherId = fatherId

class DirNode(VirtFsNode):
    def __init__(self, id : str, name : str, fatherId : str, childrenId : list[str]):
        super().__init__(id, name, fatherId)
        self.childrenId = childrenId
        self.lastUpdate : datetime = None
        

class FileNode(VirtFsNode):
    def __init__(self, id : str, name : str, fatherId : str):
        super().__init__(id, name, fatherId)
        self.lastUpdate : datetime = None

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

class VirtFs:
    def __CalcMd5(self, text : str):
        return md5(text.encode()).hexdigest()

    def __init__(self, username : str, password : str, proxy : str = None, loginCachePath : str = None):
        httpx_client_args = None
        if proxy != None:
            httpx_client_args = {
                "proxy": proxy,
                "transport": httpx.AsyncHTTPTransport(retries=1),
            }

        self.client = PikPakApi(
            username = username,
            password = password,
            httpx_client_args=httpx_client_args)
        
        self.nodes : Dict[str, VirtFsNode] = {} 
        self.loginCachePath = loginCachePath
        self.root = DirNode(None, "", None, [])
        self.currentLocation = self.root
        self.__LoginFromCache()

    def __LoginFromCache(self):
        if self.loginCachePath == None:
            return
        if not os.path.exists(self.loginCachePath):
            return
        with open(self.loginCachePath, 'r', encoding='utf-8') as file:
            content = file.read()
            token = PikpakToken.from_json(content)
            if self.client.username != token.username or self.client.password != token.password:
                logging.error("failed to load login info from cache, not match")
                return
            self.client.access_token = token.access_token
            self.client.refresh_token = token.refresh_token
            self.client.user_id = token.user_id
            self.client.encode_token()
            logging.info("successfully load login info from cache") 
    
    def __DumpLoginInfo(self):
        if self.loginCachePath == None:
            return
        with open(self.loginCachePath, 'w', encoding='utf-8') as file:
            token = PikpakToken(self.client.username, self.client.password, self.client.access_token, self.client.refresh_token, self.client.user_id)
            file.write(token.to_json())
            logging.info("successfully dump login info to cache")

    async def __RefreshAccessToken(self):
        result = await self.client.refresh_access_token()
        return json.dumps(result, indent=4)

    async def __RefreshDirectory(self, dirNode : DirNode):
        dirInfo = await self.client.file_list(parent_id = dirNode.id)
        nodes = dirInfo["files"]
        dirNode.childrenId.clear()

        for node in nodes:
            child : VirtFsNode = None
            id = node["id"]
            name = node["name"]
            
            if id in self.nodes:
                child = self.nodes[id]
            else:
                if node["kind"].endswith("folder"):
                    child = DirNode(id, name, dirNode.id, [])
                else:
                    child = FileNode(id, name, dirNode.id)
                self.nodes[id] = child

            child.name = name
            dirNode.childrenId.append(id)
        
        dirNode.lastUpdate = datetime.now()

    async def __PathToNode(self, pathStr : str, subDir : str = None) -> VirtFsNode:
        pathWalker = PathWalker(pathStr, subDir)
        current : VirtFsNode = None
        if pathWalker.IsAbsolute():
            current = self.root
        else:
            current = self.currentLocation
        
        for spot in pathWalker.pathSpots:
            if current == None:
                break
            if spot == "..":
                if current.fatherId == None:
                    current = self.root
                else:
                    current = self.nodes[current.fatherId]
                continue

            if not isinstance(current, DirNode):
                return None
            
            currentDir : DirNode = current
            if currentDir.lastUpdate == None:
                await self.__RefreshDirectory(currentDir)
            
            if spot == ".":
                continue
            else:    
                current = None
                for childId in currentDir.childrenId:
                    node = self.nodes[childId]
                    if spot == node.name:
                        current = node
                        break

        return current     

    async def __NodeToPath(self, node : VirtFsNode) -> str:
        spots : list[str] = [""]
        current = node
        while current.id != None:
            spots.append(current.name)
            if current.fatherId == None:
                break
            current = self.nodes[current.fatherId]
        spots.append("")
        return "/".join(reversed(spots))

    async def login(self):
        result = await self.client.login()
        self.__DumpLoginInfo()
        logging.debug(json.dumps(result, indent=4))
        return "Login Success" 

    async def ls(self, pathStr : str = "") -> str:
        node = await self.__PathToNode(pathStr)
        if node == None:
            return f"path not found: {pathStr}"
        if not isinstance(node, DirNode):
            return f"path is not directory"
        dirNode : DirNode = node
        result = ["==== ls ===="]
        for childId in dirNode.childrenId:
            node = self.nodes[childId]
            result.append(node.name)
        return "\n".join(result)
    
    async def cd(self, pathStr : str = "") -> str:
        node = await self.__PathToNode(pathStr)
        if node == None:
            return f"path not found: {pathStr}"
        if not isinstance(node, DirNode):
            return f"path is not directory"
        dirNode : DirNode = node
        self.currentLocation = dirNode
        return ""

    async def cwd(self) -> str:
        path = await self.__NodeToPath(self.currentLocation)
        if path == None:
            return f"cwd failed"
        return path
    
    async def geturl(self, pathStr : str) -> str:
        node = await self.__PathToNode(pathStr)
        if node == None:
            return f"path not found: {pathStr}"
        if not isinstance(node, FileNode):
            return f"path is not file"
        result = await self.client.get_download_url(node.id)
        logging.debug(json.dumps(result, indent=4))
        return result["web_content_link"]
    
    async def offdown(self, url : str, pathStr : str = "") -> str :
        node = await self.__PathToNode(pathStr)
        if node == None:
            return f"path not found: {pathStr}"
        elif not isinstance(node, DirNode):
            return f"path is not directory"

        subFolderName = self.__CalcMd5(url)
        subNode = await self.__PathToNode(pathStr, subFolderName)
        if subNode == None:
            result = await self.client.create_folder(subFolderName, node.id)
            logging.debug(json.dumps(result, indent=4))
            await self.__RefreshDirectory(node)
            subNode = await self.__PathToNode(pathStr, subFolderName)
        elif not isinstance(subNode, DirNode):
            return f"path is not directory"
        
        if subNode == None:
            return f"path not found: {pathStr}"
        elif not isinstance(subNode, DirNode):
            return f"path is not directory"
        
        result = await self.client.offline_download(url, subNode.id)
        logging.debug(json.dumps(result, indent=4))

        return subFolderName


    async def HandlerCommand(self, command):
        result = re.findall(r'"(.*?)"|(\S+)', command)
        filtered_result = [item for sublist in result for item in sublist if item]

        command = filtered_result[0]
        args = filtered_result[1:]

        method = getattr(self, command)
        if method == None:
            return f"Unknown command: {command}"
        return await method(*args)