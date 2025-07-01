# admin.py (dentro de tu app)
from django.contrib import admin
from .models import Liquidacion
from .models import Tecnicos
from .models import Tecnico
from .models import Supervisor
from .models import Produccion
from .models import Curso
from .models import ProduccionTecnico
from .models import Liquidacion
from .models import Liquidaciones

admin.site.register(Liquidacion)
admin.site.register(Tecnicos)
admin.site.register(Tecnico)
admin.site.register(Supervisor)
admin.site.register(Produccion)
admin.site.register(Curso)
admin.site.register(ProduccionTecnico)
admin.site.register(Liquidacion)
admin.site.register(Liquidaciones)
