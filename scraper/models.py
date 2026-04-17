from dataclasses import dataclass, field


@dataclass
class Product:
    store: str
    name: str
    price: float
    material: str
    url: str = ""
