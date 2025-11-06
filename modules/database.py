from random import choice, randint, shuffle
from cryptography.fernet import Fernet
from base64 import urlsafe_b64encode
from time import sleep, time
from os import path, mkdir
from loguru import logger
from hashlib import md5
import asyncio
import json

from .retry import DataBaseError
from modules.utils import get_address, WindowName, sleeping
from settings import SHUFFLE_WALLETS, BID_AMOUNTS

from cryptography.fernet import InvalidToken


class DataBase:
    def __init__(self):

        self.modules_db_name = 'databases/modules.json'
        self.report_db_name = 'databases/report.json'
        self.personal_key = None
        self.window_name = None

        self.changes_lock = asyncio.Lock()

        # create db's if not exists
        if not path.isdir(self.modules_db_name.split('/')[0]):
            mkdir(self.modules_db_name.split('/')[0])

        for db_params in [
            {"name": self.modules_db_name, "value": "[]"},
            {"name": self.report_db_name, "value": "{}"},
        ]:
            if not path.isfile(db_params["name"]):
                with open(db_params["name"], 'w') as f: f.write(db_params["value"])

        with open('input_data/proxies.txt') as f:
            self.proxies = [
                "http://" + proxy.removeprefix("https://").removeprefix("http://")
                for proxy in f.read().splitlines()
                if proxy not in ['https://log:pass@ip:port', 'http://log:pass@ip:port', 'log:pass@ip:port', '', None]
            ]

        amounts = self.get_amounts()
        logger.info(f'Loaded {amounts["modules_amount"]} modules for {amounts["accs_amount"]} accounts\n')


    def set_password(self):
        if self.personal_key is not None: return

        logger.debug(f'Enter password to encrypt privatekeys (empty for default):')
        raw_password = input("")

        if not raw_password:
            raw_password = "@karamelniy dumb shit encrypting"
            logger.success(f'[+] Soft | You set empty password for Database\n')
        else:
            print(f'')
        sleep(0.2)

        password = md5(raw_password.encode()).hexdigest().encode()
        self.personal_key = Fernet(urlsafe_b64encode(password))


    def get_password(self):
        if self.personal_key is not None: return

        with open(self.modules_db_name, encoding="utf-8") as f: modules_db = json.load(f)
        if not modules_db: return

        first_pk = list(modules_db.keys())[0]
        if not first_pk: return
        try:
            temp_key = Fernet(urlsafe_b64encode(md5("@karamelniy dumb shit encrypting".encode()).hexdigest().encode()))
            self.decode_pk(pk=first_pk, key=temp_key)
            self.personal_key = temp_key
            return
        except InvalidToken: pass

        while True:
            try:
                logger.debug(f'Enter password to decrypt your privatekeys (empty for default):')
                raw_password = input("")
                password = md5(raw_password.encode()).hexdigest().encode()

                temp_key = Fernet(urlsafe_b64encode(password))
                self.decode_pk(pk=first_pk, key=temp_key)
                self.personal_key = temp_key
                logger.success(f'[+] Soft | Access granted!\n')
                return

            except InvalidToken:
                logger.error(f'[-] Soft | Invalid password\n')


    def encode_pk(self, pk: str, key: None | Fernet = None):
        if key is None:
            return self.personal_key.encrypt(pk.encode()).decode()
        return key.encrypt(pk.encode()).decode()


    def decode_pk(self, pk: str, key: None | Fernet = None):
        if key is None:
            return self.personal_key.decrypt(pk).decode()
        return key.decrypt(pk).decode()


    def create_modules(self, mode: int):
        self.set_password()

        with open('input_data/privatekeys.txt') as f:
            privatekeys = f.read().splitlines()
        with open('input_data/proxies.txt') as f:
            proxies = f.read().splitlines()

        if len(proxies) == 0 or proxies == [""] or proxies == ["http://login:password@ip:port"]:
            logger.error('You will not use proxy')
            proxies = [None for _ in range(len(privatekeys))]
        else:
            proxies = list(proxies * (len(privatekeys) // len(proxies) + 1))[:len(privatekeys)]

        with open(self.report_db_name, 'w') as f: f.write('{}')  # clear report db

        new_modules = {
            self.encode_pk(pk): {
                "address": get_address(pk),
                "modules": [{"module_name": "opinion", "status": "to_run"} for _ in range(randint(*BID_AMOUNTS))],
                "proxy": proxy,
            }
            for pk, proxy in zip(privatekeys, proxies)
        }

        with open(self.modules_db_name, 'w', encoding="utf-8") as f: json.dump(new_modules, f)
        amounts = self.get_amounts()
        logger.info(f'Created Database for {amounts["accs_amount"]} accounts with {amounts["modules_amount"]} modules!\n')


    def get_amounts(self):
        with open(self.modules_db_name, encoding="utf-8") as f: modules_db = json.load(f)
        modules_len = sum([len(modules_db[acc]["modules"]) for acc in modules_db])

        for acc in modules_db:
            for index, module in enumerate(modules_db[acc]["modules"]):
                if module["status"] in ["failed", "cloudflare"]: modules_db[acc]["modules"][index]["status"] = "to_run"

        with open(self.modules_db_name, 'w', encoding="utf-8") as f:
            json.dump(modules_db, f)

        if self.window_name == None: self.window_name = WindowName(accs_amount=len(modules_db))
        else: self.window_name.accs_amount = len(modules_db)
        self.window_name.set_modules(modules_amount=modules_len)

        return {
            'accs_amount': len(modules_db),
            'modules_amount': modules_len,
        }


    def get_all_modules(self, unique_wallets: bool = False):
        self.get_password()
        with open(self.modules_db_name, encoding="utf-8") as f: modules_db = json.load(f)

        if not modules_db:
            return 'No more accounts left'

        all_wallets_modules = [
            {
                'privatekey': self.decode_pk(pk=encoded_privatekey),
                'encoded_privatekey': encoded_privatekey,
                'proxy': wallet_data.get("proxy"),
                'address': wallet_data["address"],
                'module_info': module_info,
                'last': module_index + 1 == len(modules_db[encoded_privatekey]["modules"])
            }
            for encoded_privatekey, wallet_data in modules_db.items()
            for module_index, module_info in enumerate(modules_db[encoded_privatekey]["modules"])
            if (
                    module_info["status"] == "to_run" and
                    (not unique_wallets or module_index + 1 == len(modules_db[encoded_privatekey]["modules"]))
            )
        ]
        if SHUFFLE_WALLETS:
            shuffle(all_wallets_modules)
        return all_wallets_modules


    async def remove_account(self, module_data: dict):
        async with self.changes_lock:
            with open(self.modules_db_name, encoding="utf-8") as f: modules_db = json.load(f)

            self.window_name.add_acc()
            if module_data["module_info"]["status"] in [True, "completed"]:
                del modules_db[module_data["encoded_privatekey"]]
            else:
                modules_db[module_data["encoded_privatekey"]]["modules"] = [
                    {**module, "status": "failed"}
                    for module in modules_db[module_data["encoded_privatekey"]]["modules"]
                ]

            with open(self.modules_db_name, 'w', encoding="utf-8") as f:
                json.dump(modules_db, f)


    async def remove_module(self, module_data: dict):
        async with self.changes_lock:
            with open(self.modules_db_name, encoding="utf-8") as f: modules_db = json.load(f)

            for index, module in enumerate(modules_db[module_data["encoded_privatekey"]]["modules"]):
                if module["module_name"] == module_data["module_info"]["module_name"] and module["status"] == "to_run":
                    self.window_name.add_module()

                    if module_data["module_info"]["status"] in [True, "completed"]:
                        modules_db[module_data["encoded_privatekey"]]["modules"].remove(module)
                    else:
                        modules_db[module_data["encoded_privatekey"]]["modules"][index]["status"] = "failed"
                    break

            if [
                module["status"]
                for module in modules_db[module_data["encoded_privatekey"]]["modules"]
            ].count('to_run') == 0:
                self.window_name.add_acc()
                last_module = True
            else:
                last_module = False

            if not modules_db[module_data["encoded_privatekey"]]["modules"]:
                del modules_db[module_data["encoded_privatekey"]]

            with open(self.modules_db_name, 'w', encoding="utf-8") as f: json.dump(modules_db, f)
            return last_module


    async def append_report(self, encoded_pk: str, text: str, success: bool = None):
        async with self.changes_lock:
            status_smiles = {True: '✅ ', False: "❌ ", None: ""}

            with open(self.report_db_name, encoding="utf-8") as f: report_db = json.load(f)

            if not report_db.get(encoded_pk): report_db[encoded_pk] = {'texts': [], 'success_rate': [0, 0]}

            report_db[encoded_pk]["texts"].append(status_smiles[success] + text)
            if success != None:
                report_db[encoded_pk]["success_rate"][1] += 1
                if success == True: report_db[encoded_pk]["success_rate"][0] += 1

            with open(self.report_db_name, 'w') as f: json.dump(report_db, f)


    async def get_account_reports(self, encoded_pk: str, get_rate: bool = False):
        async with self.changes_lock:
            with open(self.report_db_name, encoding="utf-8") as f: report_db = json.load(f)

            decoded_privatekey = self.decode_pk(pk=encoded_pk)
            account_index = f"[{self.window_name.accs_done}/{self.window_name.accs_amount}]"

            if report_db.get(encoded_pk):
                account_reports = report_db[encoded_pk]
                if get_rate: return f'{account_reports["success_rate"][0]}/{account_reports["success_rate"][1]}'
                del report_db[encoded_pk]

                with open(self.report_db_name, 'w', encoding="utf-8") as f: json.dump(report_db, f)

                logs_text = '\n'.join(account_reports['texts'])
                tg_text = f'{account_index} <b>{get_address(pk=decoded_privatekey)}</b>\n\n{logs_text}'
                if account_reports["success_rate"][1]:
                    tg_text += f'\n\nSuccess rate {account_reports["success_rate"][0]}/{account_reports["success_rate"][1]}'

                return tg_text

            else:
                return f'{account_index} <b>{get_address(pk=decoded_privatekey)}</b>\n\nNo actions'
