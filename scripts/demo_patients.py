"""
Datos 100 % ficticios para la demo de ND Neurodiversa (CRM + dashboard).

Siete pacientes con historial largo: nombre, muchas sesiones, notas clínicas
extensas y una conversación de WhatsApp cada uno. Ningún nombre, teléfono ni
dato corresponde a una persona real. Los números +1 555... no existen.

Este módulo solo contiene datos; scripts/seed_demo_data.py los siembra.

Convenciones:
  - Las notas son (días_atrás, autor, tipo, contenido); tipo "N" = nota,
    "R" = recomendación a la familia o al paciente.
  - Los menores acuden con su madre, quien escribe por WhatsApp; la ficha
    está a nombre del paciente.
  - No hay contenido de autolesión ni de riesgo: la demo del protocolo de
    seguridad se hace en vivo en el simulador.
"""

MF = "María Fernanda Ruiz"
AS = "Andrés Salas"
LT = "Lucía Torres"

N, R = "N", "R"

PATIENTS = [
    # ------------------------------------------------------------------
    {
        "name": "Sofía Martínez",
        "phone": "+18095550311",
        "therapist": MF,
        "backdate_days": 140,
        "sessions": {"count": 16, "step_days": 7, "last_days_ago": 5, "no_show_idx": []},
        "upcoming": [{"days": 2.2}],
        "notes": [
            (112, MF, N,
             "Primera consulta. Acude con su madre; Sofía tiene 9 años y cursa cuarto grado. Motivo: la maestra reporta "
             "que se distrae, pierde útiles, no termina las tareas en clase y a veces parece \"en otro mundo\". En casa "
             "los deberes tardan dos horas y terminan en llanto. Embarazo y parto sin complicaciones; desarrollo "
             "motor y lenguaje en rango esperado. Duerme bien. Tía materna con dificultades de atención. Se acuerda "
             "evaluación completa en cuatro sesiones y se pide informe escolar."),
            (98, MF, N,
             "Segunda sesión de evaluación. Se aplican pruebas de atención sostenida y memoria de trabajo. Rinde "
             "bien al inicio y cae con claridad a los 15 minutos. Buena comprensión verbal. Se muestra colaboradora, "
             "conversadora, y se frustra cuando la tarea es repetitiva. Cuenta que \"las letras se mueven\" cuando "
             "está cansada; se descarta por ahora un problema de lectura y se pide valoración visual de rutina."),
            (84, MF, N,
             "Evaluación concluida. Escalas para madre y maestra coinciden en un patrón de inatención marcado, con "
             "hiperactividad leve. La valoración visual es normal. Cumple criterios de TDAH con presentación "
             "predominantemente inatenta. No hay indicios de ansiedad ni de problemas de aprendizaje específicos. "
             "Devolución a la madre en sesión aparte; recibe el resultado con alivio y muchas preguntas sobre medicación, "
             "que se derivan a neuropediatría."),
            (70, MF, R,
             "Recomendaciones entregadas a la familia: rutina fija para las tareas en el mismo lugar y horario, "
             "bloques de 15 minutos con pausa de cinco, instrucciones de un paso a la vez, temporizador visual sobre "
             "la mesa y refuerzo inmediato de lo logrado. Reducir pantallas antes de la tarea. Se entrega a la madre "
             "una hoja resumen para compartir con la maestra."),
            (56, MF, N,
             "Inicio de la intervención. Trabajamos reconocimiento de las señales de distracción con un semáforo de "
             "atención. Sofía lo entiende bien y lo usa sola al final de la sesión. La madre reporta que el temporizador "
             "redujo las discusiones a la mitad; los deberes bajaron a 80 minutos. Reunión con la maestra por llamada: "
             "acepta sentarla cerca de la pizarra y fragmentar las instrucciones."),
            (42, MF, N,
             "Neuropediatría confirma el diagnóstico y propone iniciar medicación de prueba; la familia decide esperar "
             "un mes y reforzar primero las estrategias conductuales. Respeto la decisión y quedamos en revisar a "
             "los 30 días. Sesión centrada en autoinstrucciones y en terminar una tarea corta sin ayuda; lo logra en "
             "dos de tres intentos. Ánimo muy bueno."),
            (28, MF, N,
             "Hay avances claros en casa: los deberes duran una hora y ya no hay llanto. En la escuela mejoró la entrega "
             "de tareas, pero la maestra señala que sigue perdiendo materiales. Se practica una lista de revisión de "
             "la mochila con dibujos. Aparece un tema nuevo: se compara con una compañera \"más rápida\" y dice que es "
             "\"tonta\". Se trabaja el diálogo interno con ejemplos concretos de sus logros."),
            (14, MF, R,
             "Recomendación a la madre: cuidar que los elogios sean sobre el esfuerzo y no solo el resultado, evitar "
             "etiquetas como \"distraída\" delante de la niña y mantener la mochila preparada la noche anterior. "
             "Valorar con la escuela un ajuste razonable: más tiempo en evaluaciones escritas. Se entrega modelo de carta "
             "para solicitarlo."),
            (5, MF, N,
             "Sesión 16. Buena semana: entregó todas las tareas y recibió un reconocimiento en clase. La madre decidió "
             "iniciar la medicación a prueba con neuropediatría la semana próxima. Acordamos observar atención, apetito "
             "y sueño y llevar un registro sencillo. Pendiente: devolver a la escuela el informe actualizado y revisar "
             "en dos semanas cómo responde a la medicación."),
        ],
        "conversation": {
            "days_ago": 9, "hour": 10, "status": "CLOSED",
            "turns": [
                ("IN", "PATIENT", "Buenos días, ¿dónde queda el centro? Es que mi esposo va a llevar a la niña esta vez", "LOCATION"),
                ("OUT", "BOT", "Estamos en C/ Antonio Pasica 16, Moca, República Dominicana. Aquí tienes el mapa: https://maps.google.com/?q=C/+Antonio+Pasica+16,+Moca,+Rep%C3%BAblica+Dominicana", None),
                ("IN", "PATIENT", "Perfecto, muchas gracias", None),
            ],
        },
    },
    # ------------------------------------------------------------------
    {
        "name": "Luis Fernando Almonte",
        "phone": "+18295550322",
        "therapist": AS,
        "backdate_days": 170,
        "sessions": {"count": 18, "step_days": 7, "last_days_ago": 6, "no_show_idx": [4]},
        "upcoming": [{"days": 0.85, "reminder": "CONFIRMED"}],
        "notes": [
            (125, AS, N,
             "Primera consulta. Luis Fernando tiene 6 años; lo derivó la escuela por poco contacto visual, juego "
             "repetitivo (alinea carros por tamaño y color) y malestar intenso con el timbre y la cafetería. Habla en "
             "frases cortas y a veces repite lo que oye. Desarrollo del lenguaje algo tardío según la madre. Se "
             "calma con la misma ropa y la misma ruta al colegio. Sin antecedentes médicos relevantes. Se programa "
             "evaluación del espectro autista en tres sesiones."),
            (111, AS, N,
             "Observación en sesión de juego. Responde a su nombre de forma intermitente. Comparte el interés por "
             "los carros si se entra por su juego, pero no mira a quien le habla ni señala para mostrar. Cuando se "
             "cambió de actividad sin avisar se tiró al piso y se tapó los oídos; se recuperó en cinco minutos con "
             "un espacio tranquilo. Buena memoria visual: reconoce todas las letras."),
            (97, AS, N,
             "Evaluación concluida. Los resultados y la observación son compatibles con trastorno del espectro "
             "autista, nivel 1 de apoyo, con sensibilidad auditiva marcada y retraso leve en la comunicación social. "
             "Cociente verbal en rango medio. Devolución a los padres en sesión conjunta; la madre llora de "
             "alivio, el padre pregunta si \"se le va a quitar\". Se explica que es una forma de funcionar distinta y "
             "que hay mucho por trabajar."),
            (84, AS, R,
             "Recomendaciones iniciales: secuencia de la mañana con pictogramas, avisar los cambios con diez minutos de "
             "antelación, audífonos reductores de ruido para eventos y espacio de descanso acordado con la escuela. "
             "No forzar el contacto visual; reforzar la mirada espontánea. Mantener rutinas estables los fines de semana."),
            (70, AS, N,
             "Inicio de terapia semanal. Trabajamos turnos de juego y pedir ayuda con una tarjeta. Primera vez que "
             "dice \"quiero\" para pedir un carro sin que se lo modelen. La escuela acepta el rincón de calma y que "
             "no suene el timbre para él. La madre cuenta que por fin entró al comedor con los audífonos puestos."),
            (49, AS, N,
             "Avance en comunicación: usa frases de tres a cuatro palabras para pedir y empieza a comentar lo que ve. "
             "Sigue costando iniciar con otros niños; en el juego en pareja aguanta cuatro minutos. Se introducen "
             "historias sociales para el cumpleaños de un compañero. Reportan dos episodios de llanto intenso por "
             "cambios de ruta; ceden más rápido que antes."),
            (35, AS, N,
             "No asistió a la sesión de esa semana por enfermedad de la madre; recuperamos a la semana siguiente. Aun así se nota "
             "retroceso leve en tolerancia a los cambios. Se retoma el calendario visual y se ensaya el cambio de "
             "actividad con aviso. Fue una buena sesión y salió contento con una calcomanía de reconocimiento."),
            (21, AS, R,
             "Para la familia: ampliar el calendario visual a la semana completa; practicar un \"plan B\" en imágenes "
             "para cuando algo no sale como se esperaba; introducir un nuevo alimento por semana sin presión, ya que "
             "su alimentación es muy selectiva. Si es posible, mantener la misma persona de recogida en la escuela."),
            (6, AS, N,
             "Sesión 18. Dos compañeros lo invitaron a jugar en el recreo y fue durante diez minutos. Usa frases "
             "completas para pedir y empieza a hacer preguntas simples. Persisten la selectividad alimentaria y la "
             "sensibilidad al ruido. Próximos objetivos: conversación de ida y vuelta de tres turnos y manejo del "
             "enojo con una estrategia propia. Mañana tiene cita; se envió el recordatorio y la madre confirmó."),
        ],
        "conversation": {
            "days_ago": 0.9, "hour": 9, "status": "CLOSED",
            "turns": [
                ("OUT", "SYSTEM", "Hola, te recordamos tu cita de mañana en ND Neurodiversa. Responde SÍ para confirmar o escríbenos si necesitas cambiarla.", None),
                ("IN", "PATIENT", "Sí, confirmado, ahí estaremos", "APPOINTMENT_REMINDER_REPLY"),
                ("OUT", "BOT", "¡Perfecto! Tu cita queda confirmada. Te esperamos.", None),
            ],
        },
    },
    # ------------------------------------------------------------------
    {
        "name": "Carmen Rosario",
        "phone": "+18495550333",
        "therapist": LT,
        "backdate_days": 120,
        "sessions": {"count": 12, "step_days": 10, "last_days_ago": 4, "no_show_idx": []},
        "upcoming": [{"days": 6.1}],
        "notes": [
            (110, LT, N,
             "Primera consulta. Carmen tiene 34 años, es contadora y madre de dos niños. Consulta porque se siente "
             "desbordada: olvida citas, deja proyectos a medias, pierde llaves y teléfono, y llega tarde aunque lo "
             "intenta. Refiere ansiedad constante y mal dormir. De niña \"era soñadora\" y repitió un año. Su hijo "
             "mayor acaba de ser evaluado por atención y se reconoció en la descripción. Se acuerda evaluación "
             "de TDAH en adultos y revisión de la historia del desarrollo."),
            (96, LT, N,
             "Segunda sesión: entrevista de historia del desarrollo y escalas de síntomas en la infancia y hoy. "
             "Los síntomas de inatención y desorganización están presentes desde antes de los 12 años y en más "
             "de un ámbito. La hiperactividad es interna: no puede quedarse quieta mentalmente. La ansiedad "
             "aparece sobre todo por anticipar lo que se le va a olvidar."),
            (82, LT, N,
             "Resultados: perfil compatible con TDAH presentación inatenta en adulta, con ansiedad secundaria. Descarto "
             "depresión mayor con el cuestionario; el estado de ánimo es estable. Se explican los hallazgos y llora "
             "al relacionarlos con años de autocrítica por \"no poder con todo\". Se acuerda terapia para estrategias "
             "y psicoeducación, y consulta con psiquiatría para valorar tratamiento."),
            (68, LT, R,
             "Pautas: agenda única (digital) con alarmas, lista de tres prioridades al día, un lugar fijo para llaves "
             "y teléfono, dividir los proyectos en pasos de 25 minutos, y limitar la cafeína después de las 2 p. m. "
             "Usar \"dobles\": pedir a una compañera que le recuerde las reuniones importantes."),
            (54, LT, N,
             "Revisión de estrategias. La agenda funciona entre semana, pero los fines de semana se desorganiza. La "
             "ansiedad bajó un punto en la escala. Trabajamos la autocrítica: cuando olvida algo, se dice cosas "
             "muy duras. Se practica reformular la frase con un tono más amable. Cuenta que su pareja ha empezado "
             "a ayudar con las rutinas de los niños."),
            (40, LT, N,
             "Psiquiatría inició tratamiento de prueba. Carmen refiere mejor concentración por las mañanas y "
             "algo de insomnio en los primeros días, que se vigila con el médico. Se aprovecha el momento de mayor "
             "claridad para ordenar el trabajo atrasado con un plan de dos semanas. Resultado: entregó tres "
             "informes pendientes. Se la ve orgullosa y menos ansiosa."),
            (26, LT, N,
             "Sesión centrada en la organización del hogar y la carga mental. Hace una lista de todo lo que "
             "sostiene y se sorprende de su tamaño; se negocia con su pareja un reparto más equilibrado. "
             "Persisten los olvidos cuando está cansada. Se acuerda revisar el sueño: se acuesta casi a medianoche "
             "por \"ponerse al día\". Se propone una hora límite para el trabajo."),
            (12, LT, R,
             "Recomendaciones: hora fija para acostarse, sin pantallas la hora previa, registro semanal del sueño y "
             "la medicación, y una pausa de caminata de diez minutos al mediodía. Evitar las reuniones largas "
             "sin descanso. Traer el registro a la siguiente cita para compartirlo con psiquiatría."),
            (4, LT, N,
             "Sesión 12. Dormir mejor ha reducido la irritabilidad. Cumple con la agenda cinco de siete días. "
             "Quedan pendientes la organización de los fines de semana y su temor a que el efecto de la "
             "medicación \"se acabe\"; se valora con ella espaciar las sesiones a cada tres semanas si mantiene "
             "los avances. Próxima cita en seis días."),
        ],
        "conversation": {
            "days_ago": 8, "hour": 16, "status": "HUMAN_RESOLVED",
            "turns": [
                ("IN", "PATIENT", "Hola, desde que cambió la dosis de la medicación me cuesta dormir, ¿es normal?", "CLINICAL_QUESTION"),
                ("OUT", "BOT", "Gracias por escribir. Esto es algo que debe ver una persona del equipo clínico, así que te conecto con alguien para que te ayude lo antes posible.", None),
                ("OUT", "HUMAN", "Hola Carmen, soy del equipo de ND Neurodiversa. Es importante que lo hables con tu psiquiatra y que lo comentes con Lucía en tu próxima sesión. Si empeora antes, avísanos.", None),
            ],
            "handoff_reason": "Pregunta clínica sobre medicación y síntomas — requiere respuesta de un profesional, no del bot",
            "handoff_priority": "HIGH", "triggered_after_minutes": 1, "claimed_after_minutes": 7,
            "safety_categories": {0: "SYMPTOMS"},
        },
    },
    # ------------------------------------------------------------------
    {
        "name": "Valentina Peña",
        "phone": "+18295550355",
        "therapist": LT,
        "backdate_days": 190,
        "sessions": {"count": 20, "step_days": 7, "last_days_ago": 3, "no_show_idx": []},
        "upcoming": [{"days": 4.3}],
        "notes": [
            (140, LT, N,
             "Primera consulta. Valentina tiene 5 años y está en preescolar. Su madre consulta porque habla poco: "
             "usa unas 20 palabras, señala y dice \"eso\". Entiende órdenes de dos pasos. Llora y se agarra a la "
             "madre al entrar al salón. Audición pendiente de revisión; en el control pediátrico no hubo alertas. "
             "Juego simbólico presente pero pobre. Se solicita audiometría y se inicia terapia del lenguaje semanal."),
            (126, LT, N,
             "Audiometría normal. Comprensión adecuada para la edad; el lenguaje expresivo está por debajo de lo "
             "esperado (retraso del lenguaje expresivo). Se trabaja vocabulario de la casa y rutinas con apoyo de "
             "imágenes. Valentina se muestra más tranquila cuando el juego es con muñecas. Se pide a la madre que "
             "grabe un video corto en casa para ver cómo se comunica allí."),
            (112, LT, R,
             "Pautas para la casa: nombrar lo que hacen mientras lo hacen, ofrecer dos opciones para que "
             "elija con palabras, leer un cuento con imágenes 15 minutos diarios y evitar pedirle que repita o "
             "\"diga bien\". Reducir el tiempo de pantalla a una hora y acompañarlo con comentarios."),
            (98, LT, N,
             "Incorpora unas 15 palabras nuevas por juego simbólico (comida, animales, ropa). Aparecen las primeras "
             "frases de dos palabras (\"mamá agua\", \"quiero galleta\"). En el salón sigue llorando a la entrada; "
             "se coordina con la maestra una despedida corta y siempre igual. La madre cuenta que Valentina empieza "
             "a cantar partes de canciones."),
            (77, LT, N,
             "Mantiene la progresión: frases de tres palabras de forma espontánea en casa; en sesión necesita apoyo. "
             "Se trabajan los sonidos /p/, /m/ y /b/ en juegos de soplar y burbujas. Mejora la separación en la "
             "escuela: ya entra sin llorar la mayoría de los días. La maestra la describe como \"observadora\" y "
             "dice que juega más con otros niños."),
            (56, LT, N,
             "Introducimos preguntas (\"¿dónde está?\", \"¿qué es?\") con juego de escondite. Todavía simplifica "
             "palabras largas. Se acuerda con la madre pasar a sesiones de 45 minutos por su buena tolerancia. "
             "Valentina corrige sola una palabra que antes decía mal y se ríe, señal de buena conciencia del "
             "propio lenguaje."),
            (35, LT, R,
             "Recomendación: pedir a la maestra que la anime a contar una cosa de su día en el círculo, aunque "
             "sea con una frase corta; en casa, dedicar cinco minutos al final del día a \"contar el día\" con "
             "dibujos. Mantener la lectura diaria y preguntar siempre por el qué y el para qué."),
            (17, LT, N,
             "Sesión 18. Cuenta cuentos cortos con apoyo de imágenes, con frases de cuatro palabras y algún error "
             "de concordancia, esperable a su edad. La comprensión es plena. Los sonidos más difíciles (/r/ y /s/ "
             "en grupo) quedan para más adelante. Se mantiene la frecuencia semanal y se revisará en un mes la "
             "necesidad de continuar."),
            (3, LT, N,
             "Sesión 20. Muy buena evolución: mantiene una conversación de tres turnos y pide cosas con frases "
             "completas. En el preescolar participa en el círculo. Aparece un pequeño tartamudeo ocasional al "
             "emocionarse, que se observará sin corregirlo. Se prepara el informe para la escuela y se valora "
             "pasar a sesiones cada dos semanas."),
        ],
        "conversation": {
            "days_ago": 2, "hour": 15, "status": "HUMAN_RESOLVED",
            "turns": [
                ("IN", "PATIENT", "Buenas, me cobraron dos veces la sesión del jueves", "PAYMENT_ISSUE"),
                ("OUT", "BOT", "Entiendo. Esto lo debe revisar una persona del equipo; te conecto con alguien ahora mismo.", None),
                ("OUT", "HUMAN", "Hola, disculpa el inconveniente. Ya verifiqué y vamos a hacer el reembolso del cobro duplicado hoy mismo.", None),
            ],
            "handoff_reason": "Reporte de cobro duplicado",
            "handoff_priority": "MEDIUM", "triggered_after_minutes": 1, "claimed_after_minutes": 15,
        },
    },
    # ------------------------------------------------------------------
    {
        "name": "Ramón Gómez",
        "phone": "+18495550366",
        "therapist": AS,
        "backdate_days": 100,
        "sessions": {"count": 10, "step_days": 14, "last_days_ago": 3, "no_show_idx": [1, 3]},
        "upcoming": [{"days": 11.0}],
        "notes": [
            (101, AS, N,
             "Primera consulta. Ramón tiene 14 años y cursa segundo de bachillerato. Lo trae su madre por \"mal "
             "carácter\": se enoja ante las críticas, discute con los profesores y dio un golpe a un pupitre la "
             "semana pasada. Rinde bien en matemáticas y mal en lo que le aburre. Dice que lo traen \"a la fuerza\". "
             "No consume sustancias según la madre. Se muestra reservado, con respuestas cortas. Se acuerda "
             "trabajar el vínculo antes de hablar de problemas."),
            (87, AS, N,
             "Segunda sesión. Habla más cuando se conversa de videojuegos; los usa varias horas al día. Describe "
             "el enojo como \"una olla de presión\" que sube rápido y después se arrepiente. Hay un patrón: sube "
             "cuando siente que lo humillan frente a otros. Se aplica una escala de regulación emocional: "
             "puntuación alta en impulsividad, normal en ansiedad y ánimo. Se descarta por ahora un trastorno de conducta."),
            (73, AS, N,
             "Trabajamos el termómetro del enojo del 1 al 10 y señales físicas previas (mandíbula, calor en la cara). "
             "Ramón identifica que a partir del 6 \"ya no piensa\". Se acuerda una pausa de cinco minutos como "
             "recurso. Reporta que la usó una vez en casa y evitó una discusión. Primera sonrisa de la sesión."),
            (59, AS, R,
             "Recomendaciones para la familia: evitar corregirlo delante de otros, ofrecer una salida acordada "
             "(\"necesito un momento\"), hablar de lo ocurrido cuando ya esté calmado y no durante, y pactar "
             "límites claros con consecuencias previsibles para el uso de videojuegos en días de escuela."),
            (45, AS, N,
             "No asistió a la sesión 7; la madre avisó que \"no quiso venir\". Se conversa con ella: Ramón siente que "
             "las sesiones son un castigo. Se replantea el encuadre con él en la sesión siguiente y se le da voz para "
             "escoger la actividad. Funciona: llega a tiempo y trae un tema propio, una pelea con un amigo."),
            (31, AS, N,
             "Buen momento de la terapia. Practicamos cómo responder a una crítica sin escalar, con juego de roles. "
             "Su madre dice que hubo una sola explosión en tres semanas. El profesor de matemáticas lo describe "
             "\"más manejable\". Ramón admite que le preocupa repetir curso y que por eso se pone a la defensiva "
             "con las notas."),
            (24, AS, R,
             "Recomendación: plan de estudio por bloques cortos con pausa y pedir ayuda en las materias que se le "
             "complican; reconocer los avances en la conducta delante de él. Si la tensión sube en casa, una "
             "nota escrita en lugar de una discusión. Se acordó con la madre revisar los acuerdos cada dos semanas."),
            (3, AS, N,
             "Sesión 10 (no asistió a la 9, sin aviso). Segunda ausencia en el último mes; dice que \"se le pasó\". "
             "Conviene revisar con la familia el horario, que coincide con su entrenamiento, y su motivación. En "
             "la sesión de hoy estuvo más abierto: habló de la presión de las notas y de un conflicto con su padre "
             "por el que se siente poco escuchado. Pendiente: cambiar el horario y valorar una sesión con el padre."),
        ],
        "conversation": {
            "days_ago": 4, "hour": 18, "status": "HUMAN_RESOLVED",
            "turns": [
                ("IN", "PATIENT", "Buenas noches, soy la mamá de Ramón. Quería pedir otro horario porque el de los jueves le choca con el entrenamiento", "APPOINTMENT_CHANGE"),
                ("OUT", "BOT", "Entiendo. Un cambio de horario lo gestiona una persona del equipo; te conecto con alguien para que te ayude.", None),
                ("OUT", "HUMAN", "Hola, claro que sí. Vamos a revisar con Andrés qué día le queda mejor y te confirmamos por aquí.", None),
            ],
            "handoff_reason": "Solicitud de cambio de horario",
            "handoff_priority": "MEDIUM", "triggered_after_minutes": 1, "claimed_after_minutes": 20,
        },
    },
    # ------------------------------------------------------------------
    {
        "name": "Isabela Núñez",
        "phone": "+18095550377",
        "therapist": MF,
        "backdate_days": 110,
        "sessions": {"count": 9, "step_days": 10, "last_days_ago": 6, "no_show_idx": [0, 1]},
        "upcoming": [{"days": 0.8, "reminder": "CANCEL_OR_RESCHEDULE"}],
        "notes": [
            (86, MF, N,
             "Primera consulta. Isabela tiene 12 años y está en primero de bachillerato. Su madre consulta porque "
             "evita participar en clase, no quiere exponer, se queda sin amigas nuevas en el colegio nuevo y se queja "
             "de dolores de barriga los domingos por la noche. Perfeccionista: rehace los trabajos hasta que "
             "queden \"perfectos\" y llora si saca menos de 90. Buena alumna. Sin antecedentes médicos."),
            (76, MF, N,
             "Evaluación. Escalas de ansiedad social y generalizada: elevadas en situaciones de evaluación por "
             "otros. Ánimo estable y sin alteraciones del sueño. Los dolores abdominales los valoró el pediatra "
             "sin hallazgos. Impresión: ansiedad social con rasgos de perfeccionismo. Acuerdo con la familia: "
             "terapia cognitivo-conductual, con exposición gradual."),
            (66, MF, R,
             "Primeras pautas: respiración lenta cuatro-seis antes de participar, escribir tres frases de lo que "
             "quiere decir antes de levantar la mano, y no pedirle que se explique cuando se pone nerviosa. Para "
             "la madre: evitar la sobreprotección (hablar por ella) y celebrar los intentos, no solo el resultado."),
            (56, MF, N,
             "Construimos juntas una escalera de exposición con diez peldaños, del más fácil (saludar a una "
             "compañera) al más difícil (exponer ante el curso). Elige empezar por preguntarle la hora a alguien. "
             "Lo hace en el pasillo de la consulta con la secretaria. Se sorprende de que \"no pasó nada\". "
             "Buena disposición; se ríe de sí misma."),
            (46, MF, N,
             "Avanza en la escalera: pidió prestado un lápiz a una compañera y se sentó con un grupo en el "
             "almuerzo. Los domingos la barriga duele menos. Trabajamos el perfeccionismo con un experimento: "
             "entregar una tarea con un error intencional y ver qué pasaba. No pasó nada y la maestra ni lo notó. "
             "Isabela lo describe como \"liberador y aterrador\"."),
            (36, MF, N,
             "Ensayamos la exposición frente a la terapeuta y a la secretaria. Muy tensa al inicio; a los dos "
             "minutos se relaja. Se pacta un plan de salida: puede pausar y tomar agua. En la escuela lo "
             "hizo con éxito (nota 95) y comentó que \"respiró como en la consulta\". Fue una semana de mucha "
             "confianza."),
            (26, MF, N,
             "Sesión 7. Buen estado y avances sostenidos: participa en clase una o dos veces por semana, pidió "
             "ayuda a una profesora sin que se la ofrecieran y dejó de rehacer los trabajos más de una vez. "
             "Persiste la tendencia a estudiar de más antes de los exámenes. Se acuerda practicar la "
             "\"tarea suficientemente buena\" y un límite de tiempo de estudio. Próxima meta: la exposición oral grande."),
            (3, MF, N,
             "Nota administrativa y clínica. Faltó a las dos últimas sesiones sin avisar, después de siete "
             "completadas con muy buena asistencia. Temo que esté evitando la exposición oral que se acerca. "
             "Pendiente: llamar a la familia para saber si fue por exámenes, por horario o por motivación, y "
             "ofrecer un horario de tarde. La madre respondió al recordatorio de mañana pidiendo cambiar la cita."),
        ],
        "conversation": {
            "days_ago": 0.03, "hour": None, "status": "WAITING_FOR_HUMAN",
            "turns": [
                ("OUT", "SYSTEM", "Hola, te recordamos tu cita de mañana en ND Neurodiversa. Responde SÍ para confirmar o escríbenos si necesitas cambiarla.", None),
                ("IN", "PATIENT", "Hola, necesito cambiar la cita de mañana, Isabela tiene examen", "APPOINTMENT_CHANGE"),
                ("OUT", "BOT", "Entiendo. Para cambiar tu cita te conecto con una persona del equipo, que te ayudará lo antes posible.", None),
            ],
            "handoff_reason": "Paciente respondió al recordatorio pidiendo cancelar o reprogramar",
            "handoff_priority": "HIGH", "triggered_after_minutes": 1,
        },
    },
    # ------------------------------------------------------------------
    {
        "name": "Andrés Castillo",
        "phone": "+18295550388",
        "therapist": LT,
        "backdate_days": 150,
        "sessions": {"count": 11, "step_days": 14, "last_days_ago": 8, "no_show_idx": []},
        "upcoming": [{"days": 6.0}],
        "notes": [
            (140, LT, N,
             "Primera consulta. Andrés tiene 22 años, acaba de terminar la carrera de informática y busca su primer "
             "trabajo. Diagnóstico previo de TEA nivel 1 a los 10 años. Consulta por ansiedad ante las entrevistas "
             "de trabajo y miedo a no entender las normas no escritas de una oficina. Excelente nivel técnico y "
             "verbal; le cuesta el small talk y el tono de voz de los demás. Vive con sus padres. Sin consumo de "
             "sustancias. Se plantea terapia centrada en la transición al empleo."),
            (126, LT, N,
             "Definimos objetivos con él: preparar entrevistas, aprender a pedir aclaraciones sin sentirse "
             "\"raro\", y manejar el cansancio social. Se identifica una sensibilidad a la luz fluorescente y a los "
             "ruidos de oficina abierta. Evalúa que trabajaría mejor en remoto o con un puesto tranquilo. "
             "Se revisan las experiencias previas de prácticas, donde se sintió ignorado."),
            (112, LT, R,
             "Recomendaciones: guion de presentación de 30 segundos, practicar preguntas de entrevista con "
             "grabación, preparar dos ejemplos de logros con el método situación-tarea-resultado, y llevar un "
             "documento breve que explique sus formas de trabajo preferidas si se siente cómodo compartiéndolo."),
            (98, LT, N,
             "Entrevistas simuladas. Habla con mucha precisión técnica y poca calidez; se trabaja el inicio y "
             "el cierre. Se anima a reconocer sus fortalezas (detalle, constancia). Tras la simulación se "
             "agota y necesita diez minutos de silencio; se acuerda programar descansos en los días con "
             "entrevistas. Primera entrevista real pendiente."),
            (84, LT, N,
             "Primera entrevista real: salió \"bien en lo técnico y mal en lo humano\", según su relato. No "
             "lo seleccionaron. Analizamos qué ocurrió: se quedó callado ante una pregunta abierta de "
             "valores. Se trabaja la respuesta con ejemplos. La frustración fue moderada; usó la pausa y "
             "no se aisló."),
            (63, LT, N,
             "Segunda y tercera entrevistas. Mejoró en la presentación y el cierre. Una empresa ofrece "
             "una prueba técnica remota y la supera. Se prepara la entrevista final. Su madre cuenta que ya "
             "no se encierra en la habitación tras cada intento. Se refuerza la autonomía: él mismo organiza el "
             "calendario y los recordatorios."),
            (42, LT, N,
             "Obtiene una oferta como desarrollador junior en una empresa con modalidad híbrida. Mezcla de "
             "alegría y miedo. Se revisan los primeros días: conocer al equipo, pedir un puesto con poca "
             "iluminación, hablar con su jefe sobre cómo recibe mejor las instrucciones (por escrito). Se "
             "ensayan frases para pedir aclaraciones."),
            (21, LT, R,
             "Recomendaciones para el trabajo: tomar nota de las normas implícitas que descubra, pedir una "
             "reunión semanal corta con su responsable, usar auriculares en los momentos de concentración y "
             "reservar tiempo de descanso después de las reuniones largas. Practicar la desconexión al final del día."),
            (8, LT, N,
             "Sesión 11. Lleva seis semanas en el trabajo. Se siente competente en lo técnico; le agota la "
             "comida de equipo y el ruido del lunes. Tuvo un malentendido por un correo demasiado directo y lo "
             "resolvió disculpándose. Pendiente: gestionar el cansancio social, decidir si pide trabajar más "
             "días en remoto y solicitar un certificado de asistencia a terapia para su empleador."),
        ],
        "conversation": {
            "days_ago": 0.02, "hour": None, "status": "HUMAN_ACTIVE",
            "turns": [
                ("IN", "PATIENT", "Buenas, necesito hablar con alguien del equipo, necesito un certificado de asistencia para mi trabajo", "HUMAN_REQUEST"),
                ("OUT", "BOT", "Claro. Te conecto con una persona del equipo para que te ayude con eso.", None),
                ("OUT", "HUMAN", "Hola Andrés, soy parte del equipo de ND Neurodiversa. Con gusto te ayudamos con el certificado. ¿Para qué fechas lo necesitas?", None),
            ],
            "handoff_reason": "Paciente solicitó hablar directamente con una persona",
            "handoff_priority": "MEDIUM", "triggered_after_minutes": 1, "claimed_after_minutes": 2,
        },
    },
]