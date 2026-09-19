# Levantar el proyecto localmente (con Docker para Postgres)

1. Crea el virtual environment e instala dependencias:

   ```
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip install -r requirements.txt
   ```

2. Levanta Postgres con Docker:

   ```
   docker compose up -d
   ```

   Esto crea un Postgres 16 en `localhost:5432` con usuario/clave/DB `grip`/`grip`/`grip_platform` — exactamente lo que espera `.env.example`.

3. Copia `.env.example` a `.env`:

   ```
   cp .env.example .env
   ```

   Si ya tienes una API key de Gemini, agrégala en `GEMINI_API_KEY=`. Si no, déjala vacía — el simulador sigue funcionando, solo que cualquier pregunta que necesite IA hace handoff automático a un humano en vez de responder.

4. Corre las migraciones (crea las tablas):

   ```
   .venv/bin/alembic upgrade head
   ```

5. Siembra los datos de GRIP (terapeutas, servicios, protocolo de seguridad, etc.):

   ```
   .venv/bin/python -m scripts.seed_grip
   ```

6. Arranca el servidor:

   ```
   .venv/bin/uvicorn app.main:app --reload
   ```

7. Abre el simulador en el navegador:

   ```
   http://localhost:8000/simulator
   ```

## Comandos útiles

- Correr todos los tests: `for f in tests/test_*.py; do .venv/bin/python -m "${f%.py}" | tr / .; done` (o uno por uno, ej. `.venv/bin/python -m tests.test_orchestrator`)
- Apagar Postgres sin perder datos: `docker compose down`
- Apagar y borrar todos los datos (empezar de cero): `docker compose down -v`
