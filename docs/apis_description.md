# DETALLES DE LAS APIS
## HEADERS PARA TODAS LAS APIs
```
Accept:application/json, text/plain, */*
Accept-Language:es-419,es;q=0.9,en;q=0.8
Cache-Control:no-cache
Connection:keep-alive
Content-Type:application/json
Origin:https://procesosjudiciales.funcionjudicial.gob.ec
Pragma:no-cache
Referer:https://procesosjudiciales.funcionjudicial.gob.ec/
Sec-Fetch-Dest:empty
Sec-Fetch-Mode:cors
Sec-Fetch-Site:same-site
User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36
sec-ch-ua:"Google Chrome";v="153", "Not_A Brand";v="8", "Chromium";v="153"
sec-ch-ua-mobile:?0
sec-ch-ua-platform:"Windows"
```

### API JUECES
> DESCRIPCIÓN  
API que retorna una lista de jueces y su número de identificación.
El campo "id" de la tabla "judges" corresponde con el campo "identificacionjuez" de la respuesta de la API.

- URL: `https://api.funcionjudicial.gob.ec/MANTICORE-SERVICE/api/manticore/consulta/jueces`
- MÉTODO: GET
- PARAMETROS: NO
- BODY: NO
- EJEMPLO DE RESPUESTA:
```json
[
    {
        "idjuez": null,
        "identificacionjuez": "0101567741",
        "nombrejuez": "LOYOLA POLO EDGAR FERNANDO"
    },
    {
        "idjuez": null,
        "identificacionjuez": "0101768562",
        "nombrejuez": "GUILLEN MORENO JOSE EDMUNDO"
    },
    {
        "idjuez": null,
        "identificacionjuez": "0103143897",
        "nombrejuez": "CANTOS GUAMAN MARCELA MARIBEL"
    }
]
```

### API MATERIAS
> DESCRIPCIÓN  
API que retorna una lista de las diferentes materias a las que pertenece un juicio.
El campo "id" de la tabla "areas" corresponde con el campo "idMateria" de la respuesta de la API.

- URL: `https://api.funcionjudicial.gob.ec/EXPEL-CATALOGO-JURIDICO-SERVICE/api/catalogo/materia/getAllNemonico`
- MÉTODO: GET
- PARÁMETROS: NO
- BODY: NO
- EJEMPLO DE RESPUESTA:
```json
[
    {
        "idMateria": 19,
        "nombreMateria": "ADMINISTRATIVO"
    },
    {
        "idMateria": 7,
        "nombreMateria": "ADOLESCENTE INFRACTOR"
    },
    {
        "idMateria": 31,
        "nombreMateria": "ADOLESCENTE INFRACTOR COIP"
    }
]
```

### API JUICIOS POR MATERIAS Y JUEZ
> DESCRIPCIÓN  
API que retorna una lista de las diferentes materias de los juicios. El campo "id" de la tabla "areas" corresponde con el campo "idMateria" de la respuesta de la API.

- URL: `https://api.funcionjudicial.gob.ec/MANTICORE-SERVICE/api/manticore/consulta/procesos-judiciales-resueltos`
- MÉTODO: POST
- PARÁMETROS:
```
page:1
size:50
```
- BODY:
```json
{
    "resultadoSearch": {
        "idMateria": [
            19,
            32,
            37,
            8,
            36
        ],
        "identificacionJuez": "1714155148",
        "fechaInicio": "",
        "fechaFin": "",
        "idProvincia": ""
    }
}
```
- EJEMPLO RESPUESTA:
```json
{
    "resultadoProcesosResueltos": [
        {
            "fechaIngreso": "2023-12-01 15:54:10.663",
            "idJuicio": "17510202300650",
            "nombreDelito": "CONTRA ACTOS ADMINISTRATIVOS DE DETERMINACIÓN TRIBUTARIA",
            "estadoActual": "A"
        },
        {
            "fechaIngreso": "2023-10-23 12:36:47.043",
            "idJuicio": "17510202300589",
            "nombreDelito": "CONTRA RESOLUCIONES ADMNISTRATIVAS QUE IMPONGAN SANCIONES",
            "estadoActual": "A"
        },
        {
            "fechaIngreso": "2023-10-06 10:52:56.547",
            "idJuicio": "17510202300567",
            "nombreDelito": "CONTRA ACTOS ADMINISTRATIVOS DE DETERMINACIÓN TRIBUTARIA",
            "estadoActual": "A"
        },
        {
            "fechaIngreso": "2023-09-28 11:25:20.017",
            "idJuicio": "17510202300558",
            "nombreDelito": "EXCEPCIONES A LA COACTIVA",
            "estadoActual": "A"
        },
        {
            "fechaIngreso": "2023-09-05 16:44:18.290",
            "idJuicio": "17510202300516",
            "nombreDelito": "CONTRA RESOLUCIONES ADMNISTRATIVAS QUE IMPONGAN SANCIONES",
            "estadoActual": "A"
        },
        {
            "fechaIngreso": "2023-08-31 08:31:36.363",
            "idJuicio": "17510202300504",
            "nombreDelito": "CONTRA ACTOS ADMINISTRATIVOS DE DETERMINACIÓN TRIBUTARIA",
            "estadoActual": "A"
        }
    ],
    "totalRegistros": 666
}
```

### API JUICIO POR ID
> DESCRIPCIÓN  
API que retorna la información general de un juicio según su id.
El campo "id" de la tabla "trials" corresponde con el campo "idJuicio" de la respuesta de la API.
El campo "crime" de la tabla "trials" corresponde con el campo "nombreDelito" de la respuesta de la API.
El campo "date_admission" de la tabla "trials" corresponde con el campo "fechaIngreso" de la respuesta de la API.
El campo "action_type" de la tabla "trials" corresponde con el campo "nombreTipoAccion" de la respuesta de la API.
Los demás campos del body de la respuesta deben estar almacenados en el campo "metadata" de la tabla "trials".

- URL: `https://api.funcionjudicial.gob.ec/EXPEL-CONSULTA-CAUSAS-SERVICE/api/consulta-causas/informacion/getInformacionJuicio/{juicioId}`
- MÉTODO: GET
- PARÁMETROS: NO
- BODY: NO
- EJEMPLO RESPUESTA:
```json
[
    {
        "id": 1,
        "idJuicio": "17510202300589",
        "estadoActual": null,
        "idMateria": 36,
        "idProvincia": null,
        "idCanton": null,
        "idJudicatura": null,
        "nombreDelito": "CONTRA RESOLUCIONES ADMNISTRATIVAS QUE IMPONGAN SANCIONES",
        "fechaIngreso": "2023-10-23T17:36:47.043+00:00",
        "nombre": null,
        "cedula": null,
        "idEstadoJuicio": null,
        "nombreMateria": "CONTENCIOSO TRIBUTARIO",
        "nombreEstadoJuicio": null,
        "nombreJudicatura": null,
        "nombreTipoResolucion": null,
        "nombreTipoAccion": "ACCIÓN DE IMPUGNACIÓN",
        "fechaProvidencia": null,
        "nombreProvidencia": null,
        "nombreProvincia": null,
        "iedocumentoAdjunto": "N"
    }
]
```

### API INCIDENTES POR JUICIO
> DESCRIPCIÓN  
API que retorna un array con la información de los incidentes de un juicio según de un juicio según su id.
El campo "jurisdictional_dependence" de la tabla "incidents_trials" corresponde con el campo "nombreJudicatura" de la respuesta de la API.
El campo "city" de la tabla "incidents_trials" corresponde con el campo "ciudad" de la respuesta de la API.
El campo "created_at" de la tabla "incidents_trials" corresponde con el campo "fechaCrea" dentro del array "lstIncidenteJudicatura" de la respuesta de la API.
El campo "actor" de la tabla "incidents_trials" corresponde con el campo "lstLitiganteActor" dentro del array "lstIncidenteJudicatura" de la respuesta de la API.
El campo "defendant" de la tabla "incidents_trials" corresponde con el campo "lstLitiganteDemandado" dentro del array "lstIncidenteJudicatura" de la respuesta de la API.
Los demás campos del body de la respuesta deben estar almacenados en el campo "metadata" de la tabla "incidents_trials".

- URL: `https://api.funcionjudicial.gob.ec/EXPEL-CONSULTA-CAUSAS-CLEX-SERVICE/api/consulta-causas-clex/informacion/getIncidenteJudicatura/{juicioId}`
- MÉTODO: GET
- BODY: NO
- PARÁMETROS: NO
- EJEMPLO RESPUESTA:
```json
[
{
        "idJudicatura": "17751",
        "nombreJudicatura": "SALA ESPECIALIZADA DE LO CONTENCIOSO TRIBUTARIO DE LA CORTE NACIONAL DE JUSTICIA",
        "ciudad": "QUITO",
        "lstIncidenteJudicatura": [
            {
                "idIncidenteJudicatura": 27922445,
                "idMovimientoJuicioIncidente": 26541415,
                "idJudicaturaDestino": "17751",
                "fechaCrea": "2024-07-19T09:36:47.83",
                "incidente": 1,
                "lstLitiganteActor": [
                    {
                        "tipoLitigante": "ACTOR",
                        "nombresLitigante": "IN.DI.GI. S.A.",
                        "representadoPor": "IN.DI.GI. S.A.",
                        "idLitigante": 34212055
                    }
                ],
                "lstLitiganteDemandado": [
                    {
                        "tipoLitigante": "DEMANDADO",
                        "nombresLitigante": "Nelson Eduardo Yépez Franco, Director Distrital de Esmeraldas del Servicio Nacional de Aduanas del Ecuador",
                        "representadoPor": "Nelson Eduardo Yépez Franco, Director Distrital de Esmeraldas del Servicio Nacional de Aduanas del Ecuador",
                        "idLitigante": 41902212
                    }
                ],
                "litiganteActor": null,
                "litiganteDemandado": null
            }
        ]
    }
]
```

### API AVANCES/ACTUALIZACIONES POR JUCIO
> DESCRIPCIÓN  
API que retorna un array con la lista de los diferentes avances/actualizaciones de los incidentes de un juicio.
El campo "type" de la tabla "updates_incidents" corresponde con el campo "tipo" de cada objeto del array respuesta de la API.
El campo "content de la tabla "updates_incidents" corresponde con el campo "actividad" de cada objeto del array respuesta de la API. Es importante que este campo solo almacene el text dentro de las etiquetas HTML, y que los saltos de línea con `<br>` sean reemplazados con los saltos de linea de texto `\n`.
El campo "created_at" de la tabla "updates_incidents" corresponde con el campo "fecha" de cada objeto del array respuesta de la API.

- URL: `https://api.funcionjudicial.gob.ec/EXPEL-CONSULTA-CAUSAS-SERVICE/api/consulta-causas/informacion/actuacionesJudiciales`
- MÉTODO: POST
- PARÁMETROS: NO
- BODY
```json
{
    "idMovimientoJuicioIncidente": 25702631,
    "idJuicio": "17510202300650",
    "idJudicatura": "17510",
    "idIncidenteJudicatura": 27072481,
    "aplicativo": "web",
    "nombreJudicatura": "TRIBUNAL DISTRITAL DE LO CONTENCIOSO TRIBUTARIO CON SEDE EN EL DISTRITO METROPOLITANO DE QUITO, PROVINCIA DE PICHINCHA",
    "incidente": 1
}
```
- EJEMPLO RESPUESTA:
```json
[
    {
        "codigo": 225517369,
        "idJudicatura": "17510",
        "idJuicio": "17510202300650 ",
        "fecha": "2025-02-18T20:04:23.927+00:00",
        "tipo": "NOTIFICACION (RAZON DE NOTIFICACION) ",
        "actividad": "<p style=\"text-align: justify;\">En Quito, martes dieciocho de febrero del dos mil veinte y cinco, a partir de las quince horas y cuatro minutos, mediante boletas judiciales notifiqué el AUTO DE SUSTANCIACIÓN que antecede a: ESPINOSA LANDAZURI PAULINA SOLEDAD en el casillero electrónico No.1711879799 correo electrónico  pbermeo@bermeolaw.com.  del Dr./Ab. PABLO ESTEBAN BERMEO ANDRADE; ESPINOSA LANDAZURI PAULINA SOLEDAD en el casillero No.432 en el correo electrónico  notificacionesjudiciales@bermeolaw.com, corporate@bermeolaw.com. JOHNNY MICHAEL CHONG QUI SALAZAR, DIRECTOR DISTRITAL DE QUITO DEL SERVICIO NACIONAL DE ADUANAS DEL E en el correo electrónico  jchongqui@aduana.gob.ec. JOHNNY MICHAEL CHONG QUI SALAZAR, DIRECTOR DISTRITAL DE QUITO DEL SERVICIO NACIONAL DE ADUANAS DEL E en el casillero No.2153,  en el casillero electrónico No.1751900513 correo electrónico  dayana_212610@hotmail.com, 1346.sar@aduana.gob.ec, durdanigo@aduana.gob.ec.  del Dr./Ab. DAYANA ESTEFANÍA URDÁNIGO PALMA; PROCURADOR GENERAL DEL ESTADO en el casillero No.1200,  en el casillero electrónico No.00417010012 correo electrónico  fj-pichincha@pge.gob.ec, gloria.delatorre@pge.gob.ec.  del Dr./Ab. PROCURADURÍA GENERAL DEL ESTADO - PICHINCHA - QUITO - 0012;  No se notifica a: DIRECTOR GENERAL DEL SERVICIO NACIONAL DE ADUANA DEL ECUADOR, MARÍA GABRIELA OCHOA OCHOA, DIRECTORA NACIONAL DEL SERVICIO NACIONAL DE ADUANAS DEL ECUADOR,  por no haber señalado casillero electrónico.  Certifico:</p><br /><br /><br /><br /><br /><br /><p style=\"text-align:center\"><strong>GAVILANES  LUNA CHRISTIAN ROBERTO</strong>\r\n  </p>\n\r<p style=\"text-align:center\"><strong>SECRETARIO (A)</strong>\r\n  </p>",
        "visible": "A",
        "origen": "ProvPrimera",
        "idMovimientoJuicioIncidente": 25702631,
        "ieTablaReferencia": "ProvPrimera",
        "ieDocumentoAdjunto": "S",
        "escapeOut": "false",
        "uuid": "20250218-150383382061-593792259-136073074",
        "alias": "HBA01",
        "nombreArchivo": "17510202300650_254187984_15_4_23_P21.pdf",
        "tipoIngreso": "O",
        "idTablaReferencia": "25702631",
        "nombreUsuarioModifica": "",
        "descripcionMotivoVisible": "",
        "cargo": "",
        "registroAudDigitalizacionHistorica": 0
    }
]
```