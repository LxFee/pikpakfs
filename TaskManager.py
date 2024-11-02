from enum import Enum
from typing import Awaitable, Callable, Dict
import asyncio
import logging
import shortuuid
from PikPakFileSystem import PikPakFileSystem
from pikpakapi import DownloadStatus
import random

class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    PAUSED = "paused"

class TorrentTaskStatus(Enum):
    PENDING = "pending"
    REMOTE_DOWNLOADING = "remote"
    LOCAL_DOWNLOADING = "local"
    DONE = "done"

class FileDownloadTaskStatus(Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    DONE = "done"

class TaskBase:
    TAG = ""
    MAX_CONCURRENT_NUMBER = 5

    def __init__(self, client : PikPakFileSystem):
        self.id : str = shortuuid.uuid() 
        self.status : TaskStatus = TaskStatus.PENDING
        self.worker : asyncio.Task = None
        self.handler : Callable[..., Awaitable] = None
        self.client : PikPakFileSystem = client

    def Resume(self):
        if self.status in {TaskStatus.PAUSED, TaskStatus.ERROR}:
            self.status = TaskStatus.PENDING
    

class TorrentTask(TaskBase):
    TAG = "TorrentTask"
    MAX_CONCURRENT_NUMBER = 5

    def __init__(self, torrent : str):
        super().__init__(self)
        self.torrent_status : TorrentTaskStatus = TorrentTaskStatus.PENDING
        self.torrent : str = torrent
        self.remote_full_path : str = None
        self.remote_base_path : str = None
        self.info : str = ""

        # 和PikPak交互需要的信息
        self.node_id : str = None
        self.task_id : str = None


class FileDownloadTask(TaskBase):
    TAG = "FileDownloadTask"
    MAX_CONCURRENT_NUMBER = 5

    def __init__(self, remote_path : str, owner_id : str):
        super().__init__(self)
        self.file_download_status : FileDownloadTaskStatus = FileDownloadTaskStatus.PENDING
        self.remote_path : str = remote_path
        self.owner_id : str = owner_id

async def TaskWorker(task : TaskBase):
    try:
        if task.status != TaskStatus.PENDING:
            return
        task.status = TaskStatus.RUNNING
        await task.handler(task)
        task.status = TaskStatus.DONE
    except asyncio.CancelledError:
        task.status = TaskStatus.PAUSED
    except Exception as e:
        logging.error(f"task failed, exception occurred: {e}")
        task.status = TaskStatus.ERROR

class TaskManager:
    #region 内部实现
    def __init__(self, client : PikPakFileSystem):
        self.taskQueues : Dict[str, list[TaskBase]] = {}
        self.loop : asyncio.Task = None
        self.client = client
    
    async def _loop(self):
        while True:
            await asyncio.sleep(0.5)
            for taskQueue in self.taskQueues.values():
                notRunningTasks = [task for task in taskQueue if task.worker is None or task.worker.done()]
                runningTasksNumber = len(taskQueue) - len(notRunningTasks)
                for task in [task for task in notRunningTasks if task.status == TaskStatus.PENDING]:
                    if runningTasksNumber >= task.MAX_CONCURRENT_NUMBER:
                        break
                    task.worker = asyncio.create_task(TaskWorker(task))
                    runningTasksNumber += 1

    async def _get_task_by_id(self, task_id : str) -> TaskBase:
        for queue in self.taskQueues.values():
            for task in queue:
                if task.id == task_id:
                    return task
        return None

    #region 远程下载部分
    
    async def _append_task(self, task : TaskBase):
        queue = self.taskQueues.get(task.TAG, [])
        queue.append(task)
        self.taskQueues[task.TAG] = queue

    async def _get_torrent_queue(self):
        if TorrentTask.TAG not in self.taskQueues:
            self.taskQueues[TorrentTask.TAG] = []
        return self.taskQueues[TorrentTask.TAG]
    
    async def _get_file_download_queue(self, owner_id : str):
        if FileDownloadTask.TAG not in self.taskQueues:
            self.taskQueues[FileDownloadTask.TAG] = []
        queue = self.taskQueues[FileDownloadTask.TAG]
        return [task for task in queue if task.owner_id == owner_id]

    async def _on_torrent_task_pending(self, task : TorrentTask):
        task.node_id, task.task_id = await self.client.RemoteDownload(task.torrent, task.remote_base_path)
        task.torrent_status = TorrentTaskStatus.REMOTE_DOWNLOADING

    async def _on_torrent_task_offline_downloading(self, task : TorrentTask):
        wait_seconds = 3
        while True:
            status = await self.client.QueryTaskStatus(task.task_id, task.node_id)
            if status in {DownloadStatus.not_found, DownloadStatus.not_downloading, DownloadStatus.error}:
                task.torrent_status = TorrentTaskStatus.PENDING
                raise Exception(f"remote download failed, status: {status}")
            elif status == DownloadStatus.done:
                break
            await asyncio.sleep(wait_seconds)
            wait_seconds = wait_seconds * 1.5
        
        task.remote_full_path = await self.client.UpdateDirectory(task.remote_base_path, task.node_id)
        task.torrent_status = TorrentTaskStatus.LOCAL_DOWNLOADING

    async def _on_torrent_local_downloading(self, task : TorrentTask):
        path = task.remote_full_path
        
        if not await self.client.IsDir(path):
            await self._init_file_download_task(path, task.id) 
        else:
            # 使用广度优先遍历
            queue : list[str] = [path]
            while len(queue) > 0:
                current_path = queue.pop(0)
                for child_name in await self.client.GetChildrenNames(current_path, False):
                    child_path = await self.client.JoinPath(current_path, child_name)
                    if await self.client.IsDir(child_path):
                        queue.append(child_path)
                    else:
                        await self._init_file_download_task(child_path, task.id)
        
        # 开始等待下载任务完成
        while True:
            file_download_tasks = await self._get_file_download_queue(task.id)
            all_number = len(file_download_tasks)
            not_completed_number = 0
            paused_number = 0
            error_number = 0
            for file_download_task in file_download_tasks:
                if file_download_task.status == TaskStatus.PAUSED:
                    paused_number += 1
                if file_download_task.status == TaskStatus.ERROR:
                    error_number += 1
                if file_download_task.status in {TaskStatus.PENDING, TaskStatus.RUNNING}:
                    not_completed_number += 1
            
            running_number = all_number - not_completed_number - paused_number - error_number
            task.info = f"{running_number}/{all_number} ({paused_number}|{error_number})"
            
            if not_completed_number > 0:
                await asyncio.sleep(0.5)
                continue
            if error_number > 0:
                raise Exception("file download failed")
            if paused_number > 0:
                raise asyncio.CancelledError()
            break
            
        task.torrent_status = TorrentTaskStatus.DONE

    async def _on_torrent_task_cancelled(self, task : TorrentTask):
        file_download_tasks = await self._get_file_download_queue(task.id)
        for file_download_task in file_download_tasks:
            if file_download_task.worker is not None:
                file_download_task.worker.cancel()

    async def _torrent_task_handler(self, task : TorrentTask):
        try:
            while True:
                if task.torrent_status == TorrentTaskStatus.PENDING:
                    await self._on_torrent_task_pending(task)
                elif task.torrent_status == TorrentTaskStatus.REMOTE_DOWNLOADING:
                    await self._on_torrent_task_offline_downloading(task)
                elif task.torrent_status == TorrentTaskStatus.LOCAL_DOWNLOADING:
                    await self._on_torrent_local_downloading(task)
                else:
                    break
        except asyncio.CancelledError:
            await self._on_torrent_task_cancelled(task)
            raise
    #endregion


    #region 文件下载部分
    async def _init_file_download_task(self, remote_path : str, owner_id : str) -> str:
        queue = await self._get_file_download_queue(owner_id)
        for task in queue:
            if not isinstance(task, FileDownloadTask):
                continue
            if task.remote_path == remote_path:
                if task.status in {TaskStatus.PAUSED, TaskStatus.ERROR}:
                    task.status = TaskStatus.PENDING
                return task.id
        task = FileDownloadTask(remote_path, owner_id)
        task.handler = self._file_download_task_handler
        await self._append_task(task)
        return task.id
    
    async def _file_download_task_handler(self, task : FileDownloadTask):
        await asyncio.sleep(30)
        if random.randint(1, 5) == 2:
            raise asyncio.CancelledError()
        if random.randint(1, 5) == 3:
            raise Exception("random error")
        pass

    #endregion

    #endregion

    #region 对外接口

    def Start(self):
        # todo: 从文件中恢复任务
        if self.loop is None:
            self.loop = asyncio.create_task(self._loop())

    def Stop(self):
        if self.loop is not None:
            self.loop.cancel()
            self.loop = None
        # todo: 保存任务到文件
    
    async def CreateTorrentTask(self, torrent : str, remote_base_path : str) -> str:
        task = TorrentTask(torrent)
        task.remote_base_path = remote_base_path
        task.handler = self._torrent_task_handler
        await self._append_task(task)
        return task.id

    async def PullRemote(self, remote_full_path : str) -> str:
        if not await self.client.IfExists(remote_full_path):
            raise Exception("target not found")
        queue = await self._get_torrent_queue()
        for task in queue:
            if not isinstance(task, TorrentTask):
                continue
            if task.remote_full_path == remote_full_path:
                return task.id
        task = TorrentTask(None)
        task.remote_full_path = remote_full_path
        task.handler = self._torrent_task_handler
        task.torrent_status = TorrentTaskStatus.LOCAL_DOWNLOADING
        await self._append_task(task)
        return task.id
    
    async def QueryTasks(self, tag : str, filter_status : TaskStatus = None):
        queue = self.taskQueues.get(tag, [])
        if filter_status is None:
            return queue
        return [task for task in queue if task.status == filter_status]    
    
    async def StopTask(self, task_id : str):
        task = await self._get_task_by_id(task_id)
        if task is not None and task.worker is not None:
            task.worker.cancel()
    
    async def ResumeTask(self, task_id : str):
        task = await self._get_task_by_id(task_id)
        if task is not None:
            task.Resume()

    #endregion
