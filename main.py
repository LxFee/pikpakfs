import asyncio, nest_asyncio
import cmd2
from functools import wraps
import logging
import threading
import colorlog
from PikPakFileSystem import PikPakFileSystem
import os
from tabulate import tabulate
import types
from TaskManager import TaskManager, TaskStatus, TorrentTask, FileDownloadTask

LogFormatter = colorlog.ColoredFormatter(
        "%(log_color)s%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt='%Y-%m-%d %H:%M:%S',
        reset=True,
        log_colors={
            'DEBUG': 'cyan',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'red,bg_white',
        }
    )

def setup_logging():
    file_handler = logging.FileHandler('app.log')
    file_handler.setFormatter(LogFormatter)
    file_handler.setLevel(logging.DEBUG)
    
    logger = logging.getLogger()
    logger.addHandler(file_handler)
    logger.setLevel(logging.DEBUG)

setup_logging()
MainLoop : asyncio.AbstractEventLoop = None
Client = PikPakFileSystem(auth_cache_path = "token.json", proxy_address="http://127.0.0.1:7897")

class RunSync:
    _current_task : asyncio.Task = None

    def StopCurrentRunningCoroutine():
        if RunSync._current_task is not None:
            RunSync._current_task.cancel()

    def __init__(self, func):
        wraps(func)(self)

    def __call__(self, *args, **kwargs):
        currentLoop = None
        try:
            currentLoop = asyncio.get_running_loop()
        except RuntimeError:
            logging.error("Not in an event loop")
            pass
        func = self.__wrapped__
        if currentLoop is MainLoop:
            task = asyncio.Task(func(*args, **kwargs))
            RunSync._current_task = task
            result = MainLoop.run_until_complete(task)
            RunSync._current_task = None
            return result
        else:
            return asyncio.run_coroutine_threadsafe(func(*args, **kwargs), MainLoop).result()

    def __get__(self, instance, cls):
        if instance is None:
            return self
        else:
            return types.MethodType(self, instance)

class App(cmd2.Cmd):
    #region Console设置
    def _io_worker(self, loop):
        asyncio.set_event_loop(loop)
        loop.run_forever()

    async def input(self, prompt):
        async def _input(prompt):
            return self._read_command_line(prompt)
        future = asyncio.run_coroutine_threadsafe(_input(prompt), self.ioLoop)
        return await asyncio.wrap_future(future)

    async def print(self, *args, **kwargs):
        async def _print(*args, **kwargs):
            print(*args, **kwargs)
        future = asyncio.run_coroutine_threadsafe(_print(*args, **kwargs), self.ioLoop)
        await asyncio.wrap_future(future)

    def __init__(self):
        super().__init__()
        self.log_handler = logging.StreamHandler()
        self.log_handler.setFormatter(LogFormatter)
        self.log_handler.setLevel(logging.CRITICAL)
        logging.getLogger().addHandler(self.log_handler)

        self.task_manager = TaskManager(Client)

    def preloop(self):
        # 1. 设置忽略SIGINT
        import signal
        def signal_handler(sig, frame):
            RunSync.StopCurrentRunningCoroutine()
        signal.signal(signal.SIGINT, signal_handler)

        # 2. 创建IO线程处理输入输出
        self.ioLoop = asyncio.new_event_loop()
        self.ioThread = threading.Thread(target=self._io_worker, args=(self.ioLoop,))
        self.ioThread.start()

        # 3. 设置console
        self.saved_readline_settings = None
        with self.sigint_protection:
            self.saved_readline_settings = self._set_up_cmd2_readline()
        
        # 4. 启动任务管理器
        self.task_manager.Start()
    
    def postloop(self):
        # 1. 还原console设置
        with self.sigint_protection:
            if self.saved_readline_settings is not None:
                self._restore_readline(self.saved_readline_settings)
        
        # 2. 停止IO线程
        # https://stackoverflow.com/questions/51642267/asyncio-how-do-you-use-run-forever
        self.ioLoop.call_soon_threadsafe(self.ioLoop.stop)
        self.ioThread.join()

        # 3. 停止任务管理器
        self.task_manager.Stop()

    #endregion

    #region 所有命令
    def do_logging_off(self, args):
        """
        Disable logging
        """
        self.log_handler.setLevel(logging.CRITICAL)
        logging.critical("Logging disabled")
    
    def do_logging_debug(self, args):
        """
        Enable debug mode
        """
        self.log_handler.setLevel(logging.DEBUG)
        logging.debug("Debug mode enabled")

    def do_logging_info(self, args):
        """
        Enable info mode
        """
        self.log_handler.setLevel(logging.INFO)
        logging.info("Info mode enabled")

    login_parser = cmd2.Cmd2ArgumentParser()
    login_parser.add_argument("username", help="username", nargs="?")
    login_parser.add_argument("password", help="password", nargs="?")
    @cmd2.with_argparser(login_parser)
    @RunSync
    async def do_login(self, args):
        """
        Login to pikpak
        """
        await Client.Login(args.username, args.password)
        await self.print("Logged in successfully")

    async def _path_completer(self, text, line, begidx, endidx, ignoreFiles):   
        father_path, son_name = await Client.SplitPath(text)
        children_names = await Client.GetChildrenNames(father_path, ignoreFiles)
        matches = []
        for child_name in children_names:
            if child_name.startswith(son_name):
                self.display_matches.append(child_name)
                if son_name == "":
                    matches.append(text + child_name)
                elif text.endswith(son_name):
                    matches.append(text[:text.rfind(son_name)] + child_name)
        if len(matches) == 1 and await Client.IsDir(father_path + matches[0]):
            if matches[0] == son_name:
                matches[0] += "/"
            self.allow_appended_space = False
            self.allow_closing_quote = False
        return matches

    @RunSync
    async def complete_ls(self, text, line, begidx, endidx):
        return await self._path_completer(text, line, begidx, endidx, False)

    ls_parser = cmd2.Cmd2ArgumentParser()
    ls_parser.add_argument("path", help="path", default="", nargs="?")
    @cmd2.with_argparser(ls_parser)
    @RunSync
    async def do_ls(self, args):
        """
        List files in a directory
        """
        if await Client.IsDir(args.path):
            for child_name in await Client.GetChildrenNames(args.path, False):
                await self.print(child_name)
    
    @RunSync
    async def complete_cd(self, text, line, begidx, endidx):
        return await self._path_completer(text, line, begidx, endidx, True)

    cd_parser = cmd2.Cmd2ArgumentParser()
    cd_parser.add_argument("path", help="path", default="", nargs="?")
    @cmd2.with_argparser(cd_parser)
    @RunSync
    async def do_cd(self, args):
        """
        Change directory
        """
        await Client.SetCwd(args.path)
    
    @RunSync
    async def do_cwd(self, args):
        """
        Print current working directory
        """
        await self.print(await Client.GetCwd())

    def do_clear(self, args):
        """
        Clear the terminal screen
        """
        os.system('cls' if os.name == 'nt' else 'clear')

    @RunSync
    async def complete_rm(self, text, line, begidx, endidx):
        return await self._path_completer(text, line, begidx, endidx, False)

    rm_parser = cmd2.Cmd2ArgumentParser()
    rm_parser.add_argument("paths", help="paths", default="", nargs="+")
    @cmd2.with_argparser(rm_parser)
    @RunSync
    async def do_rm(self, args):
        """
        Remove a file or directory
        """
        await Client.Delete(args.paths)

    @RunSync
    async def complete_mkdir(self, text, line, begidx, endidx):
        return await self._path_completer(text, line, begidx, endidx, True)

    mkdir_parser = cmd2.Cmd2ArgumentParser()
    mkdir_parser.add_argument("path", help="new directory path")
    @cmd2.with_argparser(mkdir_parser)
    @RunSync
    async def do_mkdir(self, args):
        """
        Create a directory
        """
        await Client.MakeDir(args.path)

    download_parser = cmd2.Cmd2ArgumentParser()
    download_parser.add_argument("torrent", help="torrent")
    @cmd2.with_argparser(download_parser)
    @RunSync
    async def do_download(self, args):
        """
        Download a file or directory
        """
        task_id = await self.task_manager.CreateTorrentTask(args.torrent, await Client.GetCwd())
        await self.print(f"Task {task_id} created")

    @RunSync
    async def complete_pull(self, text, line, begidx, endidx):
        return await self._path_completer(text, line, begidx, endidx, False)

    pull_parser = cmd2.Cmd2ArgumentParser()
    pull_parser.add_argument("target", help="pull target")
    @cmd2.with_argparser(pull_parser)
    @RunSync
    async def do_pull(self, args):
        """
        Pull a file or directory
        """
        task_id = await self.task_manager.PullRemote(await Client.ToFullPath(args.target))
        await self.print(f"Task {task_id} created")
        

    query_parser = cmd2.Cmd2ArgumentParser()
    query_parser.add_argument("-t", "--type", help="type", nargs="?", choices=["torrent", "file"], default="torrent")
    query_parser.add_argument("-f", "--filter", help="filter", nargs="?", choices=[member.value for member in TaskStatus])
    @cmd2.with_argparser(query_parser)
    @RunSync
    async def do_query(self, args):
        """
        Query All Tasks
        """
        filter_status = TaskStatus(args.filter) if args.filter is not None else None
        if args.type == "torrent":
            tasks = await self.task_manager.QueryTasks(TorrentTask.TAG, filter_status)
            # 格式化输出所有task信息id，status，lastStatus的信息，输出表格
            table = [[task.id, task.status.value, task.torrent_status.value, task.info] for task in tasks if isinstance(task, TorrentTask)]
            headers = ["id", "status", "details", "progress"]
            await self.print(tabulate(table, headers, tablefmt="grid"))
        elif args.type == "file":
            tasks = await self.task_manager.QueryTasks(FileDownloadTask.TAG, filter_status)
            table = [[task.id, task.status.value, task.file_download_status, task.remote_path] for task in tasks if isinstance(task, FileDownloadTask)]
            headers = ["id", "status", "details", "remote_path"]
            await self.print(tabulate(table, headers, tablefmt="grid"))

    taskid_parser = cmd2.Cmd2ArgumentParser()
    taskid_parser.add_argument("task_id", help="task id")
    
    @cmd2.with_argparser(taskid_parser)
    @RunSync
    async def do_pause(self, args):
        """
        Stop a task
        """
        await self.task_manager.StopTask(args.task_id)

    @cmd2.with_argparser(taskid_parser)
    @RunSync
    async def do_resume(self, args):
        """
        Resume a task
        """
        await self.task_manager.ResumeTask(args.task_id)
    
    #endregion


#region APP入口
async def mainLoop():
    global MainLoop
    MainLoop = asyncio.get_running_loop()
    app = App()

    app.preloop()
    try:
        stop = False
        while not stop:
            line = await app.input(app.prompt)
            try:
                stop = app.onecmd_plus_hooks(line)
            except asyncio.CancelledError:
                await app.print("^C: Task cancelled")
    finally:
        app.postloop()

if __name__ == "__main__":  
    nest_asyncio.apply()
    asyncio.run(mainLoop())
#endregion