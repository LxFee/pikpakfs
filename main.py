import asyncio, nest_asyncio
import cmd2
from functools import wraps
from aioconsole import ainput, aprint
import logging
import colorlog
from pikpakFs import VirtFsNode, DirNode, FileNode, PKVirtFs
import os

def RunSyncInLoop(loop):
    def decorator(func):
        @wraps(func)
        def decorated(*args, **kwargs):
            currentLoop = None
            try:
                currentLoop = asyncio.get_running_loop()
            except RuntimeError:
                pass
            if currentLoop is loop:
                return loop.run_until_complete(func(*args, **kwargs))
            else:
                return asyncio.run_coroutine_threadsafe(func(*args, **kwargs), loop).result()
        return decorated
    return decorator

def ProvideDecoratorSelfArgs(decorator, argsProvider):
    def wrapper(func):
        @wraps(func)
        def decorated(*args, **kwargs):
            namespace = args[0]
            return decorator(argsProvider(namespace))(func)(*args, **kwargs)
        return decorated
    
    return wrapper

class PikpakConsole(cmd2.Cmd): 
    def _SetupLogging(self):
        formatter = colorlog.ColoredFormatter(
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

        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        
        logger = logging.getLogger()
        logger.addHandler(handler)

        logger.setLevel(logging.INFO)

    def __init__(self):
        super().__init__()
        self._SetupLogging()
        self.client = PKVirtFs("token.json", proxy="http://127.0.0.1:7897")
    
    async def Run(self):
        import signal
        def signal_handler(sig, frame):
            pass
        signal.signal(signal.SIGINT, signal_handler)
        self.loop = asyncio.get_running_loop()

        saved_readline_settings = None
        try:
            # Get sigint protection while we set up readline for cmd2
            with self.sigint_protection:
                saved_readline_settings = self._set_up_cmd2_readline()

            stop = False
            while not stop:
                # Get sigint protection while we read the command line
                line = await asyncio.to_thread(self._read_command_line, self.prompt)
                # Run the command along with all associated pre and post hooks
                stop = self.onecmd_plus_hooks(line)
        finally:
            # Get sigint protection while we restore readline settings
            with self.sigint_protection:
                if saved_readline_settings is not None:
                    self._restore_readline(saved_readline_settings)
    
    def do_debug(self, args):
        """
        Enable debug mode
        """
        logging.getLogger().setLevel(logging.DEBUG)
        logging.debug("Debug mode enabled")

    def do_debugoff(self, args):
        """
        Disable debug mode
        """
        logging.getLogger().setLevel(logging.INFO)
        logging.info("Debug mode disabled")

    
    def LoopProvider(self):
        return self.loop

    RunSync = ProvideDecoratorSelfArgs(RunSyncInLoop, LoopProvider)
    
    login_parser = cmd2.Cmd2ArgumentParser()
    login_parser.add_argument("username", help="username", nargs="?")
    login_parser.add_argument("password", help="password", nargs="?")

    @RunSync
    @cmd2.with_argparser(login_parser)
    async def do_login(self, args):
        """
        Login to pikpak
        """
        await self.client.Login(args.username, args.password)
        await aprint("Logged in successfully")
    
    def PathParserProvider(self):
        @RunSyncInLoop(self.loop)
        async def PathToNode(path):
            path = await self.client.PathToNode(path)
            if path is None:
                raise ValueError("Invalid path")
            return path

        path_parser = cmd2.Cmd2ArgumentParser()
        path_parser.add_argument("path", help="path", default="", nargs="?", type=PathToNode)
        return path_parser

    WithPathParser = ProvideDecoratorSelfArgs(cmd2.with_argparser, PathParserProvider)

    @RunSync
    async def PathCompleter(self, text, line, begidx, endidx, filterFiles):   
        father, sonName = await self.client.PathToFatherNodeAndNodeName(text)
        fatherDir = self.client.ToDir(father)
        if fatherDir is None:
            return []

        matches = []
        matchesNode = []
        for childId in fatherDir.childrenId:
            node = self.client.nodes[childId]
            if filterFiles and isinstance(node, FileNode):
                continue
            if node.name.startswith(sonName):
                self.display_matches.append(node.name)
                if sonName == "":
                    matches.append(text + node.name)
                elif text.endswith(sonName):
                    matches.append(text[:text.rfind(sonName)] + node.name)
                matchesNode.append(node)
        
        if len(matchesNode) == 1 and self.client.ToDir(matchesNode[0]) is not None:
            matches[0] += "/"
            self.allow_appended_space = False
            self.allow_closing_quote = False        

        return matches

    def complete_ls(self, text, line, begidx, endidx):
        return self.PathCompleter(text, line, begidx, endidx, filterFiles = False)

    @RunSync
    @WithPathParser
    async def do_ls(self, args):
        """
        List files in a directory
        """
        if isinstance(args.path, DirNode):
            for childId in args.path.childrenId:
                node = self.client.nodes[childId]
                await aprint(node.name)
        elif isinstance(args.path, FileNode):
            await aprint(args.path.name)
        else:
            await aprint("Invalid path")
    
    def complete_cd(self, text, line, begidx, endidx):
        return self.PathCompleter(text, line, begidx, endidx, filterFiles = True)

    @RunSync
    @WithPathParser
    async def do_cd(self, args):
        """
        Change directory
        """
        if self.client.ToDir(args.path) is None:
            await aprint("Invalid directory")
            return
        self.client.currentLocation = args.path
    
    @RunSync
    async def do_cwd(self, args):
        """
        Print current working directory
        """
        await aprint(self.client.NodeToPath(self.client.currentLocation))

    def do_clear(self, args):
        """
        Clear the terminal screen
        """
        os.system('cls' if os.name == 'nt' else 'clear')

    def complete_rm(self, text, line, begidx, endidx):
        return self.PathCompleter(text, line, begidx, endidx, filterFiles = False)

    @RunSync
    @WithPathParser
    async def do_rm(self, args):
        await self.client.Delete(args.path)


if __name__ == "__main__":  
    nest_asyncio.apply()
    prog = PikpakConsole()
    asyncio.run(prog.Run())