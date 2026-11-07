# Demo Center Fase 2B — 2026-11-06

Baseline 489aecd, repositorio Dwarvesmx/nea-agent; árbol limpio antes de crear la rama local demo-center-fase-2b. Decisión DC-F2B-ARQ-01: Nea ejecuta demo externa; Vocero conserva identidad/configuración y autoridad financiera única. Autorizado solo desarrollo local coordinado, sin despliegue ni efectos externos.

IMPLEMENTADO LOCALMENTE: app/demo_contract.py valida la extensión demo v1 y pertenencia org/conversación. app/dispatch.py rechaza sobre demo inválido (422) o válido sin consumidor aislado (503), tras verificar firma; nunca lo degrada al turno comercial. app/dispatch_worker.py impide recuperación y handoff comerciales para sobres con ámbito. Ningún ACK demo durable se anuncia todavía. No existe aún consumidor demo habilitable.

PROBADO: tests/test_demo_contract.py, tests/test_dispatch_durable.py y tests/test_despacho_raiz.py: 28/28, con pytest del entorno local existente, sin servicios ni modelos externos. El primer intento detectó una aserción incorrecta de la cola en memoria diferida; corregida antes de la ejecución aprobada. No se ejecutó la suite completa ni se declara certificada Fase 2B.

Pendiente: mediación financiera CRM, persistencia/ejecución demo aislada, sesiones/rutas y autorización final de salida, conciliación e integración con matriz crítica completa. No hay bloqueo de permisos ni nueva decisión arquitectónica pendiente. No modificar el turno comercial para suplir estas piezas.

Registro CRM: ../../vocero-crm-dev/docs/modonext/demo-center/09_FASE_2B.md (ruta local desde docs). Contrato espejo: src/server/demo-center/domain/channel-contract.ts. Los validadores no prueban por sí solos compatibilidad de extremo a extremo.

Sin migraciones, bases nuevas, credenciales, mensajes, generación facturable, commits, push o despliegues.

## DC-F2B-02 — avance parcial, 2026-11-06

Se preservan todos los cambios locales previos. Se corrigió la deduplicación durable de Nea: un mismo (organización, dispatchId) con sobre JSON diferente devuelve 409 y no sustituye la instantánea original. El repositorio PostgreSQL usa INSERT/ON CONFLICT RETURNING para comparar contra la fila elegida atómicamente; MemoryStore mantiene la misma semántica. No se implementó aún comparación de bytes crudos firmados, ni se afirma cumplimiento completo del contrato de snapshot/hash de ARQ-01.

Pruebas ejecutadas: tests/test_demo_contract.py, tests/test_dispatch_durable.py y tests/test_despacho_raiz.py: 29/29. La nueva prueba verifica conflicto HTTP autenticado y conservación del sobre en MemoryStore; no se ejecutó la rama PostgreSQL de esta modificación ni una integración CRM–Nea. git diff --check aprobado. Sin migraciones, llamadas externas, credenciales o producción.

Fase 2B continúa incompleta: este avance no entrega sesiones externas, mediación financiera, consumidor demo, salida/conciliación ni UI. No hay nueva autorización pendiente. No habilitar capacidades ni convertir la respuesta 503 demo_capability_unavailable en ACK exitoso hasta disponer del circuito aislado.

## DC-F2B-04 — 2026-11-06

PROBADO: CrmClient.resolve_demo_session delega en DemoSessionClient usando su HTTP autenticado existente. Contrato POST /api/bot/demo/session v1, sin acceso a tablas CRM desde Nea. Conserva requestKey proporcionado por el despacho y diferencia autorización, conflicto, disponibilidad, respuesta inválida y timeout con resultado incierto; no reintenta ni cae al flujo comercial.

Cliente real Python probado contra handler HTTP real CRM y PostgreSQL descartable por node scripts/test-demo-session-isolated.mjs desde CRM. Integración 1/1 aprobada con creación, repetición en procesos Python distintos, concurrencia, cuenta/organización ajenas, clave inválida, políticas y revocación; una sesión externa durable y ningún mensaje/despacho comercial. No prueba FastAPI/worker completos: este incremento conecta solamente el cliente de resolución. Evidencia ../tmp/demo-session-evidence/2026-11-06T08-46-02.874Z/vitest.log desde raíz del workspace. Registro detallado: ../../vocero-crm-dev/docs/modonext/demo-center/09_FASE_2B.md desde docs Nea.

8/8 pruebas unitarias del cliente aprobadas. No se cambió recepción demo (sigue bloqueada sin consumidor). Sin migraciones, credenciales reales, producción, dinero, mensajes, commits, push o despliegue. Pendientes de otras entregas: generación/mediación, consumidor y transporte; no forman parte de DC-F2B-04.

Regresión final DC-F2B-04: 37/37 pruebas combinadas de cliente, frontera demo y despacho comercial aprobadas. git diff --check aprobado.

## DC-F2B-05 — 2026-11-06

PROBADO: CrmClient.demo_finance_operation y app/demo_finance.py consumen POST /api/bot/demo/finance con la identidad de servicio existente. Contrato estricto v1 reserve/query/release/settle con org/space/session/operationKey/inputDigest; Nea no declara costos, tarifas ni asigna IDs del proveedor. Timeout es result_uncertain sin reintento automático; consultar con los mismos identificadores. No existe fallback a Lukas.

Prueba cruzada Python Nea→HTTP real→handler CRM→motor financiero compartido→PostgreSQL descartable: 1/1 con escenarios de reserva, idempotencia/concurrencia, conflicto, presupuesto, liquidación/duplicados, rechazo de costo inyectado, aislamiento, release y ACK perdido después del commit. Comando desde CRM: node scripts/test-demo-finance-http-isolated.mjs. Evidencia workspace tmp/demo-finance-http-evidence/2026-11-06T09-32-11.879Z/vitest.log. Nea tests/test_demo_finance_client.py: 4/4. Regresión financiera CRM 8/8; typecheck y diff --check aprobados.

Liquidación requiere ID/estado de ejecución registrado previamente por el backend y evidencia consultada por CRM; la prueba representa ese registro mediante fixture SQL y sustituye únicamente al proveedor. No acredita la futura ejecución/generación: ese gateway aún no está implementado. Sin generación, consumidor, despacho ni WhatsApp. Sin migraciones, bases persistentes, credenciales reales, llamadas facturables, commits/push o despliegue. Registro contractual detallado: ../../vocero-crm-dev/docs/modonext/demo-center/09_FASE_2B.md desde docs Nea.

## DC-F2B-06 — 2026-11-06 — Consumidor demo real

IMPLEMENTADO y PROBADO localmente: app/demo_consumer.py consume sobres demo v1 firmados a través de la cola/worker existentes, resuelve sesión y verifica todo el scope, reserva en CRM y orquesta CrmClient.generate_demo. Nea no recibe claves de IA ni declara costos, no utiliza perfil/memoria/seguimiento/handoff comerciales y no envía mensajes. PgDispatchStore conserva entrada y estado; resultado e historial residen en CRM por sesión y operationKey igual a dispatchId. Un reinicio repite solamente la orquestación con esa clave: el gateway no repite una generación attempted/uncertain. Incertidumbre financiera queda visible en CRM para conciliación y no implica entrega. Consumer apagado por defecto con DEMO_CONSUMER_ENABLED; modo multi_org rechazado.

POST /api/bot/demo/generate autenticado en CRM exige reserva y digest, construye contexto fijado y llama al adaptador existente antes de liquidar mediante el único motor financiero CRM. Registra el ID del proveedor desde backend: ya no se siembra en SQL para acreditar este incremento. No se amplió el contrato financiero para permitir costos arbitrarios.

Comando desde CRM: node scripts/test-demo-generation-isolated.mjs, integrada 1/1 aprobada con dos turnos, continuidad, duplicación/reinicio del cliente, dos workers, timeout/uncertain, rechazo de scope ajeno/revocación y ausencia de mensajes comerciales. El probe tests/demo_generation_probe.py usa FastAPI/HMAC reales por ASGI, HTTP real hacia CRM, asyncpg/PgDispatchStore y migración 008 existente en schema sintético nea_demo_generation de demo_f2a; no usa MemoryStore para cola. El adaptador IA es controlado. No se acreditó un kill del proceso durante el efecto remoto ni una entrega externa.

Evidencia workspace tmp/demo-generation-evidence/2026-11-06T09-48-08.174Z/vitest.log. Regresiones Nea 41/41 y financiera CRM 8/8 aprobadas; typecheck CRM y diff --check aprobados. No hay migraciones nuevas en repositorios, bases persistentes, llamadas facturables, WhatsApp, producción, commits, push o despliegues. Detalle/contratos: ../../vocero-crm-dev/docs/modonext/demo-center/09_FASE_2B.md desde docs Nea. Próximo alcance pendiente: selector de canal/entrega y conciliación, sin activación implícita.

Evidencia final DC-F2B-06 tras reforzar la revalidación del gateway CRM: workspace tmp/demo-generation-evidence/2026-11-06T09-51-37.481Z/vitest.log, integración 1/1 aprobada. Se mantiene pendiente la certificación del sobre de bytes crudos (la deduplicación vigente compara JSON) para la posterior integración de canal; este resultado no declara Fase 2B completa ni autoriza tráfico externo.

## DC-F2B-07 — 2026-11-06

No se conectó el consumidor a send_message ni al outbox comercial. La ruta CRM existente crea mensajes comerciales y su autorización no está ligada a la sesión demo; la lectura comercial inspeccionada no acredita exclusión de scope demo. Bloqueo de seguridad detallado en ../../vocero-crm-dev/docs/modonext/demo-center/09_FASE_2B.md desde docs Nea. Este incremento no está implementado ni probado; sin cambios funcionales ni envíos.

## DC-F2B-07 — 2026-11-06 — Despacho durable local probado

Resuelta la dependencia de seguridad por autorización explícita del propietario. CrmClient.dispatch_demo envía al gateway autenticado POST /api/bot/demo/delivery (nunca a Meta/Zernio), usando sessionId y operationKey del despacho original y SHA-256 de la respuesta persistida. El consumidor solo lo invoca después de generación settled, con DEMO_DELIVERY_ENABLED apagado por defecto. Timeout HTTP produce delivery_result_uncertain sin reintento ni handoff comercial. La misma identidad permite consultar/reconciliar; un reintento no vuelve a generar.

CRM reutiliza zernio_whatsapp_outbox y enviarTextoWhatsAppZernio, con mensaje scope demo y autorización propia de sesión/participante/cuenta/hilo/generación, sin perfil comercial. El backend filtra contenido/eventos comerciales y no actualiza preview/contadores comerciales al encolar demo. Estados: pending/processing/accepted/delivered/failed/cancelled/uncertain. accepted no significa delivered; un iniciado interrumpido es incierto, no se vuelve a enviar. Consulta oficial por ID exacto conocido y callbacks firmados permiten confirmar estados; sin ID/correlación fiable permanece incierto y necesita intervención. No hay garantía exactly-once externa ni endpoint para declarar arbitrariamente delivered.

PROBADO: node scripts/test-demo-delivery-isolated.mjs desde CRM, integrada 1/1 con cola Nea PostgreSQL, HTTP real Nea→CRM, ledger y outbox reales, IA y transporte controlados. Incluye confirmaciones firmadas, fallos antes del efecto, ACK perdido, incertidumbre, confirmación tardía, duplicados/concurrencia, procesos nuevos/lease interrumpido, revocación, destinatario/hilo/org ajenos y ausencia de contenido/eventos comerciales. Cinco generaciones, costo sintético 25 micro-USD sin doble cargo por reintentos. Evidencia workspace tmp/demo-delivery-evidence/2026-11-06T10-35-08.945Z/vitest.log. No equivale a WhatsApp real ni a una prueba de kill en todos los puntos de efecto.

Regresiones Nea 41/41 más cliente de entrega 2/2, CRM mensajes/transporte 95 y analítica 22 aprobadas, integración de generación anterior 1/1; typecheck/diff --check aprobados. Sin migraciones nuevas: se reutilizan tablas existentes y schema de cola sintético. Sin bases persistentes, credenciales reales, facturación, mensajes externos, producción, commits, push o despliegue. Ramas existentes y cambios previos conservados. Detalle de archivos/contratos/compatibilidad histórica: ../../vocero-crm-dev/docs/modonext/demo-center/09_FASE_2B.md desde docs Nea. La activación real y el selector de tráfico se mantienen pendientes de sus autorizaciones/pruebas específicas.


## DC-F2B-CIERRE-01 — 2026-11-07

Cierre local coordinado; no desplegado ni validado en WhatsApp real. Recuperación: inventario y archivos con SHA-256 en la carpeta local tmp/recuperacion-f2b-20261107-000341 del workspace superior; cotejar los hashes del inventario antes de restaurar archivos seleccionados, preservando los cambios posteriores. No copiar ni restaurar .env ni bases. Los SHA finales de la pareja de commits se registran en el manifiesto local de esa carpeta para evitar autorreferencias. La activación externa y selector real requieren aprobación y pruebas separadas.
