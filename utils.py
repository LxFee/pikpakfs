class PathWalker():
    def __init__(self, path : str, sep : str = "/"):
        self._path_spots : list[str] = []
        if not path.startswith(sep):
            self._path_spots.append(".")
        path_spots : list[str] = path.split(sep)
        self._path_spots.extend(path_spots)
    
    def IsAbsolute(self) -> bool:
        return len(self._path_spots) == 0 or self._path_spots[0] != "."

    def AppendSpot(self, spot):
        self._path_spots.append(spot)
    
    def Walk(self) -> list[str]:
        return self._path_spots