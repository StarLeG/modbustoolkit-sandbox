# Запуск рядом с сайтом на cPanel

Инструкция для случая, когда сайт modbustoolkit.electroman.by живёт на хостинге
с cPanel, а песочницу нужно открыть пользователям. Общие варианты запуска
(Docker, nginx, systemd) описаны в [DEPLOY.md](DEPLOY.md), здесь собран
конкретный путь под cPanel.

## Сначала главное: на обычном shared-хостинге сервер не запустится

Песочнице нужны три вещи, которых тарифы shared-хостинга с cPanel не дают:

1. **Свой TCP-порт наружу.** Modbus-клиенты подключаются к порту 5020 сырым
   TCP. На shared-хостинге входящие порты закрыты файрволом, открыть их
   может только администратор сервера.
2. **Процесс, который работает постоянно.** Движок поведений тикает каждые
   100 мс, Modbus-сервер держит соединения часами. Хостинг убивает фоновые
   процессы пользователя, а раздел «Setup Python App» запускает приложение
   через Passenger только на время HTTP-запроса.
3. **WebSocket.** Live-обновления UI идут по `/ws/state` и `/ws/events`,
   Passenger на shared-тарифах их обычно не пропускает.

Проверить свой тариф просто: если в cPanel нет доступа к root (WHM) и
поддержка не готова открыть входящий порт 5020, вам сюда, в схему с VPS
ниже. Писать в поддержку с просьбой «запустить asyncio-сервер на порту 5020»
почти всегда бесполезно.

## Рекомендуемая схема

Сайт остаётся на cPanel как есть. Песочница едет на небольшой VPS
и получает свой поддомен:

| Что | Где | Адрес |
|---|---|---|
| Лендинг, support.html, docs.html | хостинг с cPanel | `modbustoolkit.electroman.by` |
| Web UI, REST API, Modbus-TCP | VPS | `modbus-sendbox.electroman.by` (HTTPS и порт 5020) |

Поддомен нужен потому, что DNS-имя указывает на один сервер: Modbus-клиент,
подключившийся к `modbustoolkit.electroman.by:5020`, попадёт на хостинг,
где порт закрыт. Имя `modbus-sendbox` можно заменить любым другим, ниже оно
используется для примера.

Для VPS хватит минимального тарифа: 1 vCPU, 1 ГБ RAM, 10 ГБ диска,
Ubuntu 24.04 LTS, публичный IPv4. Сервер держит состояние в памяти и диск
почти не трогает.

## Шаг 1. DNS-запись в cPanel

1. Узнайте публичный IP VPS (в панели провайдера или командой `curl -4 ifconfig.me` на самой VPS).
2. В cPanel откройте **Domains → Zone Editor**.
3. Найдите в списке зону `electroman.by` (или зону поддомена,
   если она заведена отдельно) и нажмите **Manage**.
4. **Add Record → A Record**:
   - Name: `modbus-sendbox.electroman.by.` (с точкой на конце);
   - TTL: `300`;
   - Address: IP вашей VPS.
5. Если у VPS есть IPv6, добавьте так же **AAAA Record** с IPv6-адресом.
   Без IPv6 запись AAAA не создавайте.

Если зоны `electroman.by` в Zone Editor нет, DNS домена ведётся у
регистратора. Тогда ту же A-запись нужно добавить в его панели.

Проверка через пару минут (на своём компьютере):

```bash
nslookup modbus-sendbox.electroman.by
```

Ответ должен содержать IP вашей VPS.

## Шаг 2. Подготовка VPS

Подключитесь по SSH под root и обновите систему:

```bash
apt update && apt -y upgrade
apt -y install git python3 python3-venv curl ufw
```

Файрвол: SSH, HTTP и HTTPS для сайта, 5020 для Modbus.

```bash
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 5020/tcp
ufw --force enable
```

Менеджер пакетов uv ставим в системный каталог, чтобы им мог пользоваться
служебный пользователь:

```bash
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
uv --version
```

## Шаг 3. Установка песочницы

Служебный пользователь без shell и каталог приложения:

```bash
useradd --system --home /opt/modbus-test-server --shell /usr/sbin/nologin modbus-test
install -d -o modbus-test -g modbus-test /opt/modbus-test-server
```

Код из репозитория и зависимости:

```bash
git clone https://github.com/StarLeG/modbustoolkit-sandbox.git /tmp/sandbox
cp -r /tmp/sandbox/{app,pyproject.toml,uv.lock,scripts} /opt/modbus-test-server/
chown -R modbus-test:modbus-test /opt/modbus-test-server
sudo -u modbus-test uv sync --frozen --no-dev --directory /opt/modbus-test-server
```

Переменные окружения. Modbus слушает все интерфейсы, web только localhost
(наружу его отдаст Caddy на шаге 4):

```bash
install -d /etc/modbus-test-server
cat > /etc/modbus-test-server/env <<'EOF'
MODBUS_TCP_HOST=0.0.0.0
MODBUS_TCP_PORT=5020
MODBUS_PUBLIC_HOST=modbus-sendbox.electroman.by
WEB_HOST=127.0.0.1
WEB_PORT=8765
FORWARDED_ALLOW_IPS=127.0.0.1
LOG_FORMAT=json
LOG_LEVEL=info
EOF
```

Остальные переменные (лимиты, размер журнала, TTL инъекций) можно не задавать,
их дефолты подходят для публичного сервера. Полный список с пояснениями лежит
в [.env.example](../.env.example).

Служба systemd:

```bash
cp /tmp/sandbox/deploy/modbus-test-server.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now modbus-test-server
systemctl status modbus-test-server --no-pager
```

Проверка на самой VPS:

```bash
curl -s http://127.0.0.1:8765/healthz
```

Ответ `{"status":"ok"}` означает, что web и движок поднялись.

## Шаг 4. HTTPS через Caddy

Caddy сам получает и продлевает сертификат Let's Encrypt и без настроек
проксирует WebSocket. Установка из официального репозитория:

```bash
apt -y install debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
apt update && apt -y install caddy
```

Конфиг:

```bash
cat > /etc/caddy/Caddyfile <<'EOF'
modbus-sendbox.electroman.by {
    encode gzip
    reverse_proxy 127.0.0.1:8765
}
EOF
systemctl reload caddy
```

Сертификат выпускается при первом запросе, поэтому DNS-запись из шага 1 к этому
моменту уже должна работать. Если Caddy пишет в `journalctl -u caddy` ошибку
ACME, почти всегда виноваты DNS или закрытый порт 80.

## Шаг 5. Проверка снаружи

С любого своего компьютера:

1. Откройте https://modbus-sendbox.electroman.by. На «Обзоре» в поле
   «Хост» должно стоять `modbus-sendbox.electroman.by`, значения на
   «Карте регистров» должны меняться сами.
2. Проверьте Modbus. Подойдёт ModbusToolkit (хост
   `modbus-sendbox.electroman.by`, порт `5020`, Unit ID 1) или Python:

```bash
pip install "pymodbus>=3.6"
python -c "from pymodbus.client import ModbusTcpClient as C; c=C('modbus-sendbox.electroman.by', port=5020); c.connect(); print(c.read_input_registers(1, count=4, device_id=1).registers); c.close()"
```

3. Откройте https://modbus-sendbox.electroman.by/api/docs и выполните
   любой GET-запрос кнопкой «Try it out».

## Шаг 6. Ссылка с основного сайта

Посетители лендинга должны найти песочницу. В cPanel есть два способа:

- **Кнопка на лендинге.** **Files → File Manager**, каталог сайта
  (обычно `public_html` или каталог поддомена), правка `index.html`:
  добавьте ссылку на `https://modbus-sendbox.electroman.by`.
- **Короткий адрес.** **Domains → Redirects**: тип *Permanent (301)*,
  домен `modbustoolkit.electroman.by`, путь `/sandbox`, адрес
  `https://modbus-sendbox.electroman.by`. После этого
  `modbustoolkit.electroman.by/sandbox` ведёт в песочницу.

## Обновление

Новая версия из GitHub ставится так:

```bash
cd /tmp && rm -rf sandbox && git clone https://github.com/StarLeG/modbustoolkit-sandbox.git sandbox
cp -r /tmp/sandbox/{app,pyproject.toml,uv.lock,scripts} /opt/modbus-test-server/
chown -R modbus-test:modbus-test /opt/modbus-test-server
sudo -u modbus-test uv sync --frozen --no-dev --directory /opt/modbus-test-server
systemctl restart modbus-test-server
```

Перезапуск сбрасывает значения регистров и настроенные поведения к демо-набору.
Если пользователи перенастроили поведения и их жалко терять, перед
обновлением сохраните их по разделу «Бэкап конфига поведений» в
[DEPLOY.md](DEPLOY.md).

## Обслуживание

| Задача | Команда |
|---|---|
| Логи песочницы | `journalctl -u modbus-test-server -f` |
| Логи Caddy | `journalctl -u caddy -f` |
| Перезапуск | `systemctl restart modbus-test-server` |
| Счётчики (соединения, ошибки, uptime) | `curl -s http://127.0.0.1:8765/metrics` |
| Ручная проверка живости | `sudo -u modbus-test /opt/modbus-test-server/.venv/bin/python /opt/modbus-test-server/scripts/healthcheck.py --modbus-port 5020` |

## Если cPanel стоит на своём VPS с root-доступом

Когда сервер с cPanel ваш (есть WHM и root по SSH), отдельная VPS не нужна.
Шаги 2 и 3 выполняются на том же сервере с двумя отличиями:

- Файрвол на таких серверах обычно CSF, а не ufw. Добавьте `5020` в строку
  `TCP_IN` файла `/etc/csf/csf.conf` и выполните `csf -r`.
- Порты 80 и 443 заняты веб-сервером cPanel, поэтому Caddy не ставится.
  Создайте поддомен в **cPanel → Domains**, выпустите для него сертификат
  через **SSL/TLS Status** (AutoSSL) и проксируйте его на `127.0.0.1:8765`.
  Для Apache это делается в **WHM → Apache Configuration → Include Editor**
  директивами `ProxyPass` для HTTP и `ws://` для путей `/ws/`, нужны модули
  `mod_proxy` и `mod_proxy_wstunnel`. Каждая версия WHM устроена здесь
  немного по-своему, поэтому этот вариант стоит согласовать с поддержкой
  хостинга.
