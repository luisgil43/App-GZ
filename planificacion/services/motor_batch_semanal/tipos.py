from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SitioMotor:
    sitio_planificado_id: int
    sitio_id: int

    id_claro: str
    nombre: str

    comuna: str
    tipo_zona: str

    latitud: Optional[float]
    longitud: Optional[float]

    condicion_acceso: str

    estado_permiso: str
    prioridad: str

    urbano: bool = False
    rural: bool = False

    score_acceso: float = 0.0


@dataclass
class ClusterMotor:
    id_cluster: str

    sitios: list[SitioMotor] = field(default_factory=list)

    centro_latitud: Optional[float] = None
    centro_longitud: Optional[float] = None

    radio_km: float = 0.0

    urbanos: int = 0
    rurales: int = 0

    distancia_media_km: float = 0.0
    distancia_maxima_km: float = 0.0

    score_compactacion: float = 0.0


@dataclass
class PropuestaBatchMotor:
    codigo: str

    principales: list[SitioMotor] = field(default_factory=list)

    reservas: list[SitioMotor] = field(default_factory=list)

    clusters: list[ClusterMotor] = field(default_factory=list)

    score_geografico: float = 0.0
    score_capacidad: float = 0.0
    score_acceso: float = 0.0
    score_balance_mensual: float = 0.0
    score_respaldo: float = 0.0

    score_total: float = 0.0

    motivo: str = ""

    metricas: dict = field(default_factory=dict)
