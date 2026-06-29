# Руководство по развёртыванию MVP на VPS/VDS

Данное руководство описывает минимальный способ развёртывания проекта Financial Owner Assistant на Ubuntu 22.04 или 24.04 с Docker и Docker Compose.

## 1. Требования к серверу

Рекомендуемые характеристики сервера:

- Ubuntu 22.04 или 24.04
- 2 vCPU
- 4 GB RAM
- 40+ GB SSD
- публичный IP
- Docker
- Docker Compose Plugin

## 2. Подготовка сервера

Обновите пакеты системы:

```bash
sudo apt update && sudo apt upgrade -y
```

Установите Docker:

```bash
sudo apt install -y ca-certificates curl gnupg lsb-release
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Проверьте установку:

```bash
docker --version
docker compose version
```

## 3. Клонирование проекта из GitHub

```bash
git clone https://github.com/finnik82-75/financial-owner-assistant.git
cd financial-owner-assistant
```

## 4. Создание .env из .env.example

```bash
cp .env.example .env
```

Отредактируйте файл `.env` и укажите минимум:

```bash
nano .env
```

Пример переменных:

```bash
OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-4o-mini
OPENAI_TEMPERATURE=0.2
OPENAI_MODEL_FAST=gpt-4o-mini
OPENAI_MODEL_STRONG=gpt-4o
```

## 5. Заполнение OPENAI_API_KEY

Укажите действительный ключ OpenAI API в `.env`:

```bash
OPENAI_API_KEY=your_openai_api_key
```

## 6. Создание директорий для данных

```bash
mkdir -p data/uploads data/parsed data/outputs
```

## 7. Запуск приложения

```bash
docker compose up --build -d
```

## 8. Проверка контейнера

Проверьте состояние контейнера:

```bash
docker ps
docker compose logs
```

## 9. Проверка веб-интерфейса

Откройте в браузере:

```text
http://SERVER_IP:8010
```

## 10. Остановка и перезапуск

Остановка:

```bash
docker compose down
```

Перезапуск:

```bash
docker compose restart
```

## 11. Обновление проекта из GitHub

```bash
git pull
docker compose up --build -d
```

## 12. Безопасность

Рекомендуется соблюдать следующие правила:

- `.env` не коммитить в Git
- пользовательские отчёты не коммитить
- директории `data/uploads`, `data/parsed`, `data/outputs` не публиковать и не добавлять в публичные репозитории

## 13. Следующий production-этап

После MVP можно перейти к более производительному и безопасному размещению:

- Nginx или Caddy
- домен
- SSL Let's Encrypt
- закрытие прямого доступа к порту 8010 при необходимости
