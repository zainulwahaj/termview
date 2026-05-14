import sys
from abc import ABC, abstractmethod

from PIL import Image


class BaseRenderer(ABC):
    @abstractmethod
    def render(self, img: Image.Image) -> str:
        """Return the escape-code string that displays *img* in the terminal."""

    def display(self, img: Image.Image) -> None:
        sys.stdout.write(self.render(img))
        sys.stdout.flush()
