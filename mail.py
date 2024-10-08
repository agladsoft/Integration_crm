import random
import json
import imaplib2
import email
import logging
import os.path
import time
from email.header import decode_header
import datetime
from email.message import Message
from threading import current_thread

import charset_normalizer as cn
from bs4 import BeautifulSoup, Tag
from crm import CrmClient
from tinydb import TinyDB, Query
import re
from typing import List, Tuple, Union
from log import logger
from dateutil.parser import parse
# from multiprocessing import Lock


class LocalDB:
    """
    Класс для создания, обращения, записи и удаления данных в базе данных.
    Методы:
    append_local_db - добавляет данные в базу данных по каждому пользователю.
    delete_by_date - очищает базу данных по установленному интервалу времени(по умолчанию один день)
    find - статический метод для проверки файла в json в который записываются данные по пользователю,
     проверка производится по email пользователя
    """

    def __init__(self, name=None):
        if name is not None:
            self.file_name: str = self.find(name)
            # self.lock = Lock()
            # with self.lock:
            self.fix_json(self.file_name)
            self.id_list: list = [i['id'] for i in TinyDB(self.file_name, indent=4).all()]

    @staticmethod
    def fix_json(file_path):
        with open(file_path, 'r', encoding='utf-8') as file:
            content = file.read()
        try:
            data = json.loads(content)
            return
        except json.JSONDecodeError as e:
            fixed_content = content.rsplit('},')
            fixed_content = '},'.join(fixed_content[:-1])[:-1] + '}}}'
            try:
                data = json.loads(fixed_content)
                with open(file_path, 'w', encoding='utf-8') as file:
                    json.dump(data, file, indent=4, ensure_ascii=False)
                return data
            except json.JSONDecodeError as e:
                return None

    @staticmethod
    def find(name: str) -> str:
        """Определение существует ли json с таким пользователем
        :param name : email пользователя
        """
        name: str = name + '.json'
        path: str = 'email_users/'
        if not os.path.exists(path):
            os.makedirs(path)
        for root, dirs, files in os.walk(path):
            if not files:
                with open(path + name, 'a') as f:
                    return f.name
            if name in files:
                return path + name
            else:
                with open(path + name, 'a') as f:
                    return f.name

    def append_local_db(self, list_date_title: list, flag_select: str) -> None:
        """
        Добавление данных в базу данных по пользователю
        :param list_date_title: список с id, date, box сообщения
        :param flag_select: Директория от куда взято сообщение
        :return:
        """
        for i in list_date_title:
            if len(i) == 2:
                massage_id, date = i
                with TinyDB(self.file_name, indent=4) as db:
                    if not db.contains(Query().id == massage_id):
                        # with self.lock:
                        db.insert(
                            {'id': massage_id, 'Date': date, 'Box': flag_select})
        logger.info(
            f"{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| Write email in DATABASE")

    def delete_by_date(self) -> None:
        """
        Очистка данных в базе данных которым больше двух дней
        :return:
        """
        for root, dirs, files in os.walk('email_users/'):
            for name in files:
                try:
                    with TinyDB(root + name, indent=4) as db:
                        date: str = (datetime.date.today() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
                        logger.info(
                            f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| Delete last date {name}')
                        query = Query()
                        db.remove(query.Date < date)
                except Exception as ex:
                    logger.info(f'Ошибка при чтении файла {name}: {ex}')

    @staticmethod
    def delete_by_date_user(name):
        root: str = 'email_users/'
        name: str = name + '.json'
        try:
            with TinyDB(root + name, indent=4) as db:
                date: str = (datetime.date.today() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
                logger.info(
                    f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| Delete last date {name}')
                query: Query = Query()
                db.remove(query.Date < date)
        except Exception as ex:
            logger.info(f'Ошибка при чтении файла {name}. Ошибка : {ex}')


def change_charset(text: str, char: str) -> str:
    """
    Замена в файле html параметра charset на utf - 8 для раскодировки данных браузером при открытие html страницы
    :param text: Html сообщения пользователя
    :param char: Параметр char из html страницы
    :return: text : Страница html с изменёнными данными
    """
    try:
        soup: BeautifulSoup = BeautifulSoup(text, 'html.parser')
        charset: Tag = soup.find('meta')
        if charset and charset.get('content') is not None and 'charset' in charset.get('content').lower():
            tag: dict = charset.attrs
            tag['content'] = tag['content'].split(';')[0] + ';charset=' + char
            return soup.prettify()
        return text
    except RecursionError:
        logging.error(f"RecursionError occur for text {text} and char {char}")
        return text


def check_email(email_user: str) -> bool:
    """
    Валидация почты пользователя
    :param email_user: email пользователя
    :return: Действителен ли email
    """
    pat: re = "^[a-zA-Z0-9-_.]+@[a-zA-Z0-9]+\.[a-z]{1,3}$"
    if re.match(pat, email_user):
        return True
    return False


class Mail:
    """
    Класс Mail предназначен для чтения сообщений, обработки данных из сообщения, раскодировки данных, и передачу на запись в CRM.
    Методы:
         connect_email - основная функция для подключения к серверу чтения и передачи для данных для записи
         mail_read - Чтение сообщения после подключения к серверу
         mail_write - передача сообщения для записи в CRM
         for_massage - получение сообщения из объекта imap
         get_message_id_date - получение данных из сообщения(id, date)
         get_message_title_text_file - Получение заголовка и тела сообщения
         get_title - Раскодировка заголовка сообщения
         get_text - Раскодировка тела сообщения
         get_date - Получение даты в едином формате времени
         get_file - Получение файла из тела сообщения(в разработке, не функционирует)
         get_sender_recipients - получение данных по отправителям и получателям
         get_inbox_sent - Получения названия директорий на сервере для обращения к ним (входящие и отправленные)
         find_pattern - Нахождение номера контрагента или заявки

    """

    def __init__(self):
        self.list_table = None
        self.server = 'imap.mail.ru'
        self.today = datetime.datetime.now()
        self.yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
        self.date_format = "%d-%b-%Y"
        self.request_date_today = f'(SINCE "{self.today.strftime(self.date_format)}")'
        self.request_date_yesterday = f'(SINCE "{self.yesterday.strftime(self.date_format)}") (BEFORE "{self.today.strftime(self.date_format)}")'
        self.local_db = LocalDB()

    def connect_email(self, user: tuple) -> None:
        """
        Подключение к серверу через библиотеку imap для чтения сообщений.
        Чтение сообщений из почты
        :return: None
        """
        try:
            mail_login = user[0]
            mail_password = user[1]
            try:
                time.sleep(random.choice([1, 2, 3]))
                logger.info(
                    f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| Will create imap object...')
                imap = imaplib2.IMAP4_SSL(self.server, timeout=60)
                logger.info(
                    f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| Will login {mail_login}...')
                imap.login(mail_login, mail_password)
            except Exception as ex:
                logger.info(
                    f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| No connection {mail_login} wrong password or login'
                    f'{ex}')
                return
            self.list_table: List[dict] = LocalDB(mail_login).id_list
            INBOX, SENT = self.get_inbox_sent(imap.list())
            logger.info(
                f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| Connect Email Inbox {mail_login}')
            imap.select(INBOX, readonly=True)
            if self.mail_read(user=mail_login, imap=imap, date=self.request_date_today, flag_select='INBOX'):
                self.mail_read(user=mail_login, imap=imap, date=self.request_date_yesterday, flag_select='INBOX')
            logger.info(
                f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| Connect Email Send {mail_login}')
            imap.select(SENT, readonly=True)
            if self.mail_read(user=mail_login, imap=imap, date=self.request_date_today, flag_select='SEND'):
                self.mail_read(user=mail_login, imap=imap, date=self.request_date_yesterday, flag_select='SEND')
            imap.close()
            imap.logout()
            self.local_db.delete_by_date_user(mail_login)
        except Exception as e:
            logger.exception(f"Unexpected error occur: {e}")

    def mail_read(self, user: str, imap: imaplib2.IMAP4_SSL, date: datetime, flag_select=None) -> bool:
        """
        Получение сообщений из почты, выборка данных из сообщения: дата, id, если id присутствует в базе данных пропускаем
         сообщение, если нет то получаем заголовок, тело сообщения в формате html, получателей и отправителей
          и передаём сообщение для записи.
        :param user: Email пользователя для записи данных в базу данных
        :param imap: Объект imaplib2 в котором содержатся данные о сообщение(id, дата, заголовок, тело сообщения)
        :param date: Дата для выборки данных из почты
        :param flag_select: Директория сообщений(входящие, исходящие)
        :return: Если было получено новое сообщение которое ранее не обрабатывалось, возвращаем True если нет False
        """
        # res: str
        msg: list
        count: int = 0
        flag: bool = False
        list_date_title: list = []
        logger.info(
            f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| Will search for messages...')
        list_posts: list = sorted(imap.search(None, date)[1][0].split(), reverse=True)
        for i, post in enumerate(list_posts, 1):
            try:
                res, msg = imap.fetch(post, '(RFC822)')
            except Exception as ex:
                logger.info(
                    f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| error read massage ',
                    str(ex))
                continue
            try:
                if isinstance(msg[0][1], int):
                    msg = self.for_massage(msg)
                else:
                    msg = email.message_from_bytes(msg[0][1])
            except (Exception, AttributeError) as ex:
                logger.info(
                    f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| error read massage ',
                    str(ex))
                try:
                    msg = self.for_massage(msg)
                except Exception as exx:
                    logger.info(
                        f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| {msg} error {exx}')
                    continue
            massage_id, date = self.get_message_id_date(msg)
            if massage_id in self.list_table:
                break
            sender, recipients = self.get_sender_recipients(msg)
            title = self.get_message_title_file(msg)
            list_date_title.append((massage_id, date))
            if self.check_opportunity(title):
                continue
            text = self.get_message_text_file(msg)
            if self.mail_write(title, text, recipients, sender, flag_select, user=user):
                count += 1
        else:
            flag: bool = True

        LocalDB(user).append_local_db(list_date_title, flag_select=flag_select)
        self.list_table: List[str] = LocalDB(user).id_list
        if count == 0:
            logger.info(
                f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| No new massages')
        else:
            logger.info(
                f"{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| Write {count} massages successful")
        return flag

    def check_opportunity(self, title):
        if "#" not in title:
            return True
        return all(self.find_deal(deal) for deal in title.split("#"))

    def mail_write(self, title: str, text: str, recipients: List[str], sender: str, flag: str, user=None) -> bool:
        """
        Нахождение в заголовке данных по сделке или контрагенту через тег '#',
         если контр агент или номер сделки действителен то отправляем сообщение для записи в CRM
        :param title: Заголовок сообщения
        :param text: Текст сообщения в формате html
        :param recipients: Получатели сообщения
        :param sender: Отправитель
        :param flag: Директория сообщения(Входящие или отправленные)
        :param user: email пользователя по которому происходит выборка
        :return: При удачной записи мы возвращаем True в противном случае False
        """
        logger.info(
            f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| {user} (title: {title})')
        title_end: List[str] = title.split('#')
        if len(title_end) > 1:
            value: dict = {
                'text': text,
                'subject': title,
                'recipients': recipients,
                'sender': sender,
                'flag': flag
            }
            for tit in title_end[1:]:
                res: str = '-'.join(self.find_deal(tit))
                if not res:
                    logger.info(
                        f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| {user} res is empty')
                    continue
                logger.info(
                    f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| {user} write crm #=  {res}')
                if res[0] in ['K', 'К']:
                    logger.info(
                        f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| {user} res is {res} - will try to update_contact_post_account')
                    if CrmClient().update_contact_post_account(res, value, user=user):
                        logger.info(
                            f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| {user} res is {res} - update_contact_post_account returned True')
                        return True
                elif res[0] == 'П':
                    logger.info(
                        f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| {user} res is {res} - will try to update_contact_post_opportunity')
                    if CrmClient().update_contact_post_opportunity(res, value, user=user):
                        logger.info(
                            f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| {user} res is {res} - update_contact_post_opportunity returned True')
                        return True
            logger.info(
                f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| Не найден #номер_проекта и #номер_контрагента')
        return False

    def for_massage(self, massage: Message) -> imaplib2:
        """
        При возникновении ошибки при получении данных проходим через цикл и получаем сообщение из списка кортежей
        :param massage: Список кортежей с объектами сообщений
        :return: Объект imaplib2 в котором содержатся данные о сообщение(id, дата, заголовок, тело сообщения)
        """
        logger.info(f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| error massage')
        for n, m in enumerate(massage):
            try:
                msg: imaplib2 = email.message_from_bytes(massage[n][1])
                return msg
            except Exception as ex:
                logger.info(f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| {ex}')
                continue

    def get_message_id_date(self, msg: Message) -> Tuple[str, str]:
        """
        Получение id сообщения и даты сообщения
        :param msg: Объект imaplib2 в котором содержатся данные о сообщение(id, дата, заголовок, тело сообщения)
        :return: id сообщения и даты
        """
        try:
            massage_id: str = msg['message-ID'].strip('<>') if msg['message-ID'] else None
        except Exception as ex:
            logger.info(
                f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| Ошибка получения id {ex}')
            massage_id: str = 'id не получен'
        try:
            date: str = self.get_date(msg['DATE'])
        except TypeError as ex:
            date: str = str(datetime.datetime.utcnow())
        return massage_id, date

    def get_message_title_file(self, msg: Message) -> str:
        """
        Получение данных из объекта imap заголовка сообщения и его текст
        :param msg: Сообщение в котором содержится
        :return: текст заголовка и текст сообщения в формате html
        """
        try:
            title: str = self.get_title(decode_header(msg['SUBJECT'])[0]) if msg["Subject"] else 'По умолчанию'
        except Exception as ex:
            logger.info(
                f"{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| {ex} Ошибка заголовка")
            title: str = 'По умолчанию'
        return title

    def get_message_text_file(self, msg: Message) -> str:
        """
        Получение данных из объекта imap заголовка сообщения и его текст
        :param msg: Сообщение в котором содержится
        :return: текст заголовка и текст сообщения в формате html
        """
        text: str = ''
        try:
            for i in msg.walk():
                if i.get_content_maintype() == 'text' and i.get_content_subtype() == 'html':
                    html: str = f"{self.get_text(i)}"
                    text: str = html.replace("b'", "")
        except Exception as ex:
            logger.info(
                f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| Ошибка при получение тела сообщения {ex}')
        return text

    def get_title(self, title: Union[bytes, tuple]) -> str:
        """
        Производим раскодировку данных в формат utf-8
        :param title:Заголовок в bytes формате или в строковом
        :return: Раскодированный заголовок
        """
        if type(title) == bytes:
            try:
                return str(title, 'utf-8')
            except UnicodeDecodeError:
                return title
        if len(title) == 2:
            subject, encoding = title
            if encoding is None:
                if type(subject) == bytes:
                    try:
                        return subject.decode('utf-8')
                    except Exception as ex:
                        return 'По умолчанию'
                return subject
            else:
                try:
                    return subject.decode(encoding)
                except LookupError as e:
                    logger.error(
                        f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| Error during subject decode {subject}: {e}')

                    subject: str = ' '.join(list(subject))
                    return subject

    def get_text(self, msg: Message) -> str:
        """
        Производим раскодировку сообщение при помощи библиотеки charset_normalizer и
         метода detect где мы определяем кодировку
        :param msg:Объект imaplib2 в котором содержатся данные о сообщение(id, дата, заголовок, тело сообщения)
        :return: Раскодированное тело сообщения
        """
        text: bytes = msg.get_payload(decode=True)
        detect: dict = cn.detect(text)
        try:
            text_result: str = text.decode(detect['encoding'])
            result: str = change_charset(text_result, 'utf-8')
        except (AttributeError, TypeError) as e:
            logger.info(
                f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| Error during text decode {text}: {e}')

            if text is None:
                return ''
            result: str = change_charset(text, 'utf-8')

        return result

    def get_date(self, date: str) -> str:
        """
        Приводим к единому формату дату
        :param date:Дата из сообщения
        :return: Приведённая к еденному формату дата
        """
        try:
            date: str = parse(date).strftime('%Y-%m-%d')
            return date
        except Exception as e:
            logger.error(
                f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| Ошибка при парсинге1 даты  {date}: {e}')
            try:
                date: str = date.split(',')[1].replace('(', '').replace(')', '')[:-10]
                date_obj: datetime = parse(date)
                date_str: str = date_obj.strftime('%Y-%m-%d')
            except Exception as e:
                logger.error(
                    f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| Ошибка при парсинге2 даты  {date}: {e}')

                date_str = datetime.datetime.now().strftime('%Y-%m-%d')
            return date_str

    def get_file(self, msg):
        """
        На этапе доработки
        :param msg:
        :return:
        """
        try:
            file_name = decode_header(msg.get_filename())[0][0].decode()
        except Exception:
            logger.error(
                f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| Ошибка при попытке decode_header msg {msg}')
            file_name = msg.get_filename()
        if bool(file_name):
            file_path = os.path.join(f'/home/uventus/PycharmProjects/New_Proect/Integration_crm/{file_name}')
            if not os.path.isfile(file_path):
                with open(file_path, 'wb') as f:
                    f.write(msg.get_payload(decode=True))

    def get_sender_recipients(self, msg: Message) -> Tuple[str, List[str]]:
        """
        Получение из сообщения отправителя и получателей
        :param msg:Объект imaplib2 в котором содержатся данные о сообщение(id, дата, заголовок, тело сообщения)
        :return: Отправитель, список получателей
        """
        ADDR_PATTERN: re = re.compile('<(.*?)>')
        try:
            sender: str = msg['From'].split('=')[-1].replace('<', '').replace('>', '').strip() if not msg[
                'Return-path'].strip(
                '<>') else \
                msg['Return-path'].strip('<>')
        except Exception as ex:
            logger.info(
                f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| Ошибка в получение отправителя1 {ex}')
            try:
                sender: str = msg['From'].split('<')[1].replace('>', '')
            except Exception as ex:
                logger.info(
                    f'{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| Ошибка в получение отправителя2 {ex}')
                sender: str = 'Отправитель не получен'
        recipients: list = []
        addr_fields: list = ['To', 'Cc', 'Bcc']

        for f in addr_fields:
            rfield: str = msg.get(f, "")  # Empty string if field not present
            rlist: list = re.findall(ADDR_PATTERN, rfield)
            recipients.extend(rlist)

        return sender, recipients

    def get_inbox_sent(self, lst: Tuple[str, list]) -> Tuple[str, str]:
        try:
            for i in lst[1]:
                msg: list = i.decode().split('/')
                if 'Inbox' in msg[0]:
                    inbox: str = ''.join([i for i in msg[1] if i.isalnum()])
                elif 'Sent' in msg[0]:
                    sent: str = msg[1].replace('"', '').replace("'", '')

            return inbox, sent
        except Exception as ex:
            logger.info(
                f"{datetime.datetime.now().replace(microsecond=0)}|Thread {current_thread().ident}| Ошибка {ex}")
            inbox: str
            sent: str
            inbox, sent = 'INBOX', 'SENT'
            return inbox, sent

    def find_deal(self, deal: str) -> str:
        """
        На вход поступает номер сделки с различными символами которые нужно очистить
        :param deal: Номер сделки
        :return: Валидный номер сделки
        """
        import re
        pattern = r"([А-ЯA-Z])-([А-ЯA-Zа-яa-z]+)-(\d+)"
        matches = re.findall(pattern, deal)
        return matches[0] if len(matches) >= 1 else ''
