from random import randint
from loguru import logger
from time import sleep
import asyncio
import os

from modules import *
from modules.utils import async_sleep
from modules.retry import DataBaseError, SoftError
from settings import THREADS, SLEEP_AFTER_ACCOUNT


async def run_modules(
        mode: int,
        module_data: dict,
        sem: asyncio.Semaphore,
):
    async with address_locks[module_data["address"]]:
        async with sem:
            try:
                browser = Browser(
                    proxy=module_data['proxy'],
                    address=module_data['address'],
                    db=db,
                )
                wallet = Wallet(
                    privatekey=module_data["privatekey"],
                    encoded_pk=module_data["encoded_privatekey"],
                    db=db,
                )
                module_data["module_info"]["status"] = await Opinion(wallet=wallet, browser=browser).run(mode=mode)

            except DataBaseError:
                module_data = None
                raise

            except Exception as err:
                logger.error(f'[-] Soft | {wallet.address} | Global error: {err}')
                await db.append_report(encoded_pk=module_data["encoded_privatekey"], text=str(err), success=False)

            finally:
                if type(module_data) == dict:
                    await browser.close_sessions()
                    if mode  == 1:
                        await db.remove_module(module_data)
                    else:
                        await db.remove_account(module_data)

                    reports = await db.get_account_reports(encoded_pk=module_data["encoded_privatekey"])
                    await TgReport().send_log(logs=reports)

                    await async_sleep(randint(*SLEEP_AFTER_ACCOUNT))


async def runner(mode: int):
    all_modules = db.get_all_modules(unique_wallets=mode in [2, 3])
    sem = asyncio.Semaphore(THREADS)

    if all_modules != 'No more accounts left':
        await asyncio.gather(*[
            run_modules(
                mode=mode,
                module_data=module_data,
                sem=sem,
            )
            for module_data in all_modules
        ])

    logger.success(f'All accounts done.')
    return 'Ended'


if __name__ == '__main__':
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        db = DataBase()

        while True:
            mode = choose_mode()

            match mode.type:
                case "database":
                    db.create_modules(mode=mode.soft_id)

                case "module":
                    if asyncio.run(runner(mode=mode.soft_id)) == "Ended": break
                    print('')


        sleep(0.1)
        input('\n > Exit\n')

    except DataBaseError as e:
        logger.error(f'[-] Database | {e}')

    except SoftError as e:
        logger.error(f'[-] Soft | {e}')

    except KeyboardInterrupt:
        pass

    finally:
        logger.info('[•] Soft | Closed')



