import json
import logging

from flask import Flask, request, jsonify
import requests
import re

import config

app = Flask(__name__)
port = config.PORT
token = config.LOOP_BOT_TOKEN
loop_url = config.LOOP_URL
channel_id = config.CHANNEL_ID


@app.route('/webhook', methods=['POST'])
def webhook():
    loop = LoopService()
    try:
        loop.handle_webhook()
        return jsonify({"message": "Webhook processed successfully"}), 200  # Возвращаем успешный ответ
    except Exception as e:
        logging.exception("Error processing webhook")
        return jsonify({"error": str(e)}), 500  # Возвращаем ошибку 500 в случае исключения


class LoopService:
    def handle_webhook(self):
        # Обрабатываем последнее сообщение. Определяем, есть ли в отчёте упавшие тесты. Если есть, то получаем текст
        # сообщения и его post_id
        last_failed = self.detect_last_failed()
        if last_failed:
            try:
                # получаем дифф (список новых упавших тестов в сравнении с предыдущим тегом)
                root_id = last_failed['post_id']
                diff = self.get_diff()
                #  разбиваем сообщение на части, если его длина больше лимита loop в 4000 символов
                if diff:
                    messages = self.split_message_into_chunks(diff["message"])
                    if messages[0]:
                        self.send_msg(
                            f'Сравнил этот тег с предыдущим *{diff["previous_tag_name"]}*. Новые упавшие тесты:\n :point_down:',
                            root_id
                        )
                        for i in range(len(messages)):
                            message = "\n".join(messages[i])
                            formatted_message = f'```{message}```'
                            self.send_msg(formatted_message, root_id)
                    else:
                        self.send_msg(
                            f'Сравнил этот тег с предыдущим *{diff["previous_tag_name"]}*. Новых упавших тестов не появилось :clap:',
                            root_id
                        )
                else:
                    return jsonify({"message": "diff not available"}), 400  # Возвращаем ошибку 400 для отсутствия diff
            except Exception as e:
                logging.exception("Error in handle_webhook")
                return jsonify({"error": str(e)}), 500  # Возвращаем ошибку 500 в случае исключения

        return jsonify({"message": "diff received successfully"}), 200  # Возвращаем успешный ответ, если всё ок

    def detect_last_failed(self):
        data = request.json
        text_value = data.get('text')
        post_id = data.get('post_id')
        if 'failedRerunTests.txt' not in text_value:
            print("Упавших тестов нет, diff отсутствует")
            return None
        else:
            return {
                "text": text_value,
                "post_id": post_id,
            }

    def send_msg(self, message, root_id):
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            f'Authorization': f'Bearer {token}'
        }
        url = f'{loop_url}/api/v4/posts'

        payload = json.dumps({
            "channel_id": channel_id,
            "message": message,
            "root_id": root_id,
        })

        requests.request("POST", url, headers=headers, data=payload)

    def get_diff(self):
        compare = self.get_data_for_compare()
        if not compare:
            return None

        last_failed_tests = self.fetch_lines_from_url(compare["last_failed_tests_url"])
        previous_failed_tests = self.fetch_lines_from_url(compare["previous_failed_tests_url"])
        previous_set = set(previous_failed_tests)

        diff = [result for result in last_failed_tests if result not in previous_set]
        if diff:
            return {
                "message": diff,
                "diff_count": len(diff),
                "previous_tag_name": compare["previous_tag_name"]
            }
        else:
            return None

    def get_data_for_compare(self):
        last_failed = self.detect_last_failed()
        if last_failed:
            reports = self.get_failed_reports()
            filtered = self.filter_by_group(last_failed['text'], reports)
            if filtered:
                return self.get_messages_to_compare(last_failed['text'], filtered)

    def get_failed_reports(self):
        headers = {
            'Accept': 'application/json',
            f'Authorization': f'Bearer {token}'
        }
        url = f'{loop_url}/api/v4/channels/{channel_id}/posts'
        # получаем все сообщения в канале (по умолчанию в респонсе приходят последние 60)
        response = requests.request("GET", url, headers=headers)

        if response.status_code == 200:
            data = response.json()
            # берём массив order. В нём лежат id сообщений по порядку. Сами сообщения в респонсе лежат не по порядку.
            order = data["order"]

            # отправляем запрос для получения сообщений по порядку
            url2 = f'{loop_url}/api/v4/posts/ids'
            payload = json.dumps(order)

            response2 = requests.request("POST", url2, headers=headers, data=payload)
            resp = response2.json()
            # Извлекаем сообщения
            messages = [item['message'] for item in resp]
            # Фильтрация сообщений, содержащих 'failedRerunTests.txt'
            filtered_messages = [msg for msg in messages if re.search(r'failedRerunTests\.txt', msg)]
            return filtered_messages
        else:
            print(f"Ошибка: {response.status_code} - {response.text}")

    def filter_by_group(self, last_failed, messages):
        if re.search(r".*-acceptance.*", last_failed) and not re.search(r".*master--acceptance.*", last_failed):
            pattern = re.compile(r'.*-acceptance.*')
            return [m for m in messages if pattern.match(m)]
        elif re.search(r".*-api.*", last_failed):
            pattern = re.compile(r'.*-api.*')
            return [m for m in messages if pattern.match(m)]
        elif re.search(r".*-backend.*", last_failed):
            pattern = re.compile(r'.*-backend.*')
            return [m for m in messages if pattern.match(m)]
        else:
            print("Тег не подходит под условия")
            return None

    def get_messages_to_compare(self, last_tag_message, messages):
        previous_tag_message = None
        previous_tag_name = None
        pattern = r'(http://\S+failedRerunTests\.txt?)(?:\]|\s*$)'
        # Находим все совпадения по регулярному выражению
        last_failed_tests_url = re.search(pattern, last_tag_message).group(1)
        last_tag_name = re.search(r"\d+(-\w+)?-master(-\w+)?", last_tag_message).group()
        previous_messages = messages[1:]
        for message in previous_messages:
            if re.search(r"\d+-canary.*", message) or re.search(r"--acceptance.*", message):
                continue
            tag_name = re.search(r"\d+(-\w+)?-master(-\w+)?", message).group()
            if tag_name != last_tag_name:
                previous_tag_message = message
                previous_tag_name = tag_name
                break

        previous_failed_tests_url = re.search(pattern, previous_tag_message).group(1)
        if last_failed_tests_url and previous_failed_tests_url and previous_tag_name:
            return {
                "last_failed_tests_url": last_failed_tests_url,
                "previous_failed_tests_url": previous_failed_tests_url,
                "previous_tag_name": re.search(r"\d+(-\w+)?-master(-\w+)?", previous_tag_name).group()
            }
        else:
            return None

    def fetch_lines_from_url(self, url):
        """
        Загружает текстовый файл по указанному URL и возвращает его содержимое в виде списка строк.

        :param url: URL текстового файла.
        :return: Список строк из текстового файла.
        """
        try:
            # Отправляем GET-запрос по указанному URL
            response = requests.get(url)
            response.raise_for_status()  # Проверка на наличие ошибок HTTP

            # Разбиваем содержимое на строки и возвращаем список
            lines = response.text.splitlines()
            return lines

        except requests.exceptions.RequestException as e:
            print(f"Ошибка при загрузке файла: {e}")
            return []

    def split_message_into_chunks(self, messages, max_length=4000):
        """
        Разбивает список строк на части, чтобы каждая часть не превышала заданную длину.

        :param messages: Список строк для отправки.
        :param max_length: Максимальная длина сообщения (по умолчанию 4000).
        :return: Список списков строк.
        """
        # Объединяем все строки в одну
        combined_message = "\n".join(messages)

        # Проверяем длину объединенного сообщения
        if len(combined_message) <= max_length:
            return [messages]  # Если длина меньше или равна максимальной, возвращаем оригинальный список

        # Разбиваем сообщение на части
        chunks = []
        current_chunk = []

        for message in messages:
            # Проверяем длину текущего сообщения и добавляем его в текущий кусок
            if len("\n".join(current_chunk + [message])) <= max_length:
                current_chunk.append(message)
            else:
                # Если текущий кусок превышает максимальную длину, добавляем его в список кусочков
                chunks.append(current_chunk)
                current_chunk = [message]  # Начинаем новый кусок с текущего сообщения

        # Добавляем последний кусок, если он не пустой
        if current_chunk:
            chunks.append(current_chunk)

        return chunks


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=port)
