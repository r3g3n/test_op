from random import uniform, randint, random, choice
from datetime import datetime, timezone
from decimal import Decimal
from loguru import logger
from time import time
import asyncio

from modules.retry import CustomError, retry, TransactionError
from modules.utils import round_cut, async_sleep, make_border
from modules.browser import Browser
from modules.wallet import Wallet
from settings import BID_SETTINGS, SLEEP_BETWEEN_ORDERS, BID_TYPES, LIMIT_SETTINGS


class Opinion:

    TYPED_DATA: dict = {
        "primaryType": "Order",
        "types": {
            "EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"}],
            "Order": [
                {"name": "salt", "type": "uint256"}, {"name": "maker", "type": "address"},
                {"name": "signer", "type": "address"}, {"name": "taker", "type": "address"},
                {"name": "tokenId", "type": "uint256"}, {"name": "makerAmount", "type": "uint256"},
                {"name": "takerAmount", "type": "uint256"}, {"name": "expiration", "type": "uint256"},
                {"name": "nonce", "type": "uint256"}, {"name": "feeRateBps", "type": "uint256"},
                {"name": "side", "type": "uint8"}, {"name": "signatureType", "type": "uint8"}
            ]
        },
        "domain": {
            "name": "OPINION CTF Exchange",
            "version": "1",
            "chainId": 56,
            "verifyingContract": "0x5f45344126d6488025b0b84a3a8189f2487a7246"
        },
        "message": {
            "taker": "0x0000000000000000000000000000000000000000",
            "expiration": "0",
            "nonce": "0",
            "feeRateBps": "0",
            "signatureType": "2",
        },
    }

    def __init__(self, wallet: Wallet, browser: Browser):
        self.wallet = wallet
        self.browser = browser

        self.profile_info = None
        self.proxy_wallet = None


    @retry(source="Opinion")
    async def run(self, mode: int):
        status = None
        await self.login()

        if mode == 1:
            status = await self.buy_sell_position()

        elif mode == 2:
            status = await self.sell_all()

        elif mode == 3:
            status = await self.parse()

        return status


    async def login(self):
        if not await self.browser.is_user_registered():
            raise CustomError("User is not registered")

        date_now = datetime.now(timezone.utc)
        nonce = randint(65535, 0xffffffffffff)
        sign_message = f"""app.opinion.trade wants you to sign in with your Ethereum account:
{self.wallet.address}

Welcome to opinion.trade! By proceeding, you agree to our Privacy Policy and Terms of Use.

URI: https://app.opinion.trade
Version: 1
Chain ID: 56
Nonce: {nonce}
Issued At: {date_now.isoformat()[:-9] + 'Z'}"""
        signature = self.wallet.sign_message(sign_message).removeprefix("0x")

        await self.browser.user_login(
            sign_message,
            signature,
            int(date_now.timestamp()),
            nonce,
        )

        self.profile_info = await self.browser.get_profile_info()
        self.proxy_wallet = self.profile_info["multiSignedWalletAddress"].get("56")
        if not self.proxy_wallet:
            raise CustomError(f'No proxy wallet created')
        elif not await self.browser.is_approved(self.proxy_wallet):
            raise CustomError("Wallet is not approved")


    async def buy_sell_position(self):
        buy_order_data = await self.create_order(
            order_side="buy",
            order_type=choice(BID_TYPES["open"]),
        )
        await async_sleep(randint(*SLEEP_BETWEEN_ORDERS))

        sell_order_data = await self.create_order(
            order_side="sell",
            order_type=choice(BID_TYPES["close"]),
            event=buy_order_data["event"],
            order=buy_order_data["order"],
        )

        profit = round(float(sell_order_data["order"]["totalPrice"]) - float(buy_order_data["order"]["totalPrice"]), 2)
        volume = round(float(buy_order_data["order"]["totalPrice"]) + float(sell_order_data["order"]["totalPrice"]), 2)

        await self.wallet.db.append_report(
            encoded_pk=self.wallet.encoded_pk,
            text=f"\n🎰 <b>Profit {profit}$\n📌 Volume {volume}$</b>",
        )

        return True


    async def sell_all(self):
        sold_any = False

        open_orders = await self.browser.get_orders(order_type="limit")
        for open_order in open_orders:
            await self.browser.cancel_order(open_order["transNo"])
            pos_name = f'{open_order["mutilTitle"]} {open_order["topicTitle"]}' if open_order["mutilTitle"] else open_order["topicTitle"]
            self.log_message(f'Cancelled order in "{pos_name}"', level="INFO")
            await self.wallet.db.append_report(
                encoded_pk=self.wallet.encoded_pk,
                text=f'cancel order "{pos_name}"',
                success=True,
            )
            sold_any = True

        positions = await self.browser.get_position()
        for position in positions:
            if float(position["value"]) >= 1:
                await self.create_order(
                    order_side="sell",
                    order_type=choice(BID_TYPES["close"]),
                    position=position,
                )
                sold_any = True

        if not sold_any:
            self.log_message(f"No positions found to sell", level="INFO")
            await self.wallet.db.append_report(
                encoded_pk=self.wallet.encoded_pk,
                text="no positions found to sell",
                success=True,
            )

        return True


    async def parse(self):
        balance = round(float(self.profile_info["balance"][0]["balance"]), 2)
        profit = round(float(self.profile_info["totalProfit"]), 2)
        volume = round(float(self.profile_info["Volume"]), 2)

        positions, rank = await asyncio.gather(*[
            self.browser.get_position(),
            self.browser.get_rank(),
        ])
        total_positions = len([p for p in positions if float(p["value"]) >= 1])

        log_text = ({
            "Rank": rank,
            "Volume": volume,
            "Positions": total_positions,
            "Total Balance": f"{balance}$",
            "Profit": f"{profit}$",
        })
        self.log_message(f"Account statistics:\n{make_border(log_text)}", level="SUCCESS")
        tg_log = f"""💎 Rank: {rank}
📈 Volume: {volume}$
📌 Positions: {total_positions}
💰 Total Balance: {balance}$
💵 Profit: {profit}$
"""
        await self.wallet.db.append_report(
            encoded_pk=self.wallet.encoded_pk,
            text=tg_log
        )

        return True


    async def create_order(
            self,
            order_side: str,
            order_type: str,
            event: dict = None,
            order: dict = None,
            position: dict = None,
    ):
        if order_side == "buy":
            side = 0
            if not event:
                event = await self.browser.get_events()
                if not event:
                    raise Exception(f'No events found')

            amount = float(await self.calculate_order_amount())
            usd_amount = amount
            if event.get("force_vote"):
                event_choice_index = event["force_vote"] - 1
            else:
                event_choice_index = choice([0, 1])
            token_id = event["tokens"][event_choice_index]
            label = event["labels"][event_choice_index]

            action_name = "Bidding"

        elif order_side == "sell":
            side = 1
            if position:
                event_choice_index = position["outcomeSide"] - 1
                label = position["outcome"]
                event = await self.browser.get_events(
                    event_to_find={
                        "link": f"?topicId={position['mutilTopicId'] or position['topicId']}{'&type=multi' if position['mutilTopicId'] else ''}",
                        "event_name": position["topicTitle"],
                        "vote": position["outcomeSide"],
                    },
                )

            elif order and event:
                event_choice_index = order["outcomeSide"] - 1
                position = await self.browser.get_position(
                    topic_id=event["raw_event"]["topicId"],
                    outcome_side=order["outcomeSide"]
                )
                if not position:
                    raise Exception(f'Failed to found active position "{event["name"]}"')

                label = event["labels"][event_choice_index]

            else:
                raise Exception(f'One of `position` or `order` & `event` must be provided for sell')

            amount = float(round_cut(position["tokenAmount"], 2))
            usd_amount = round_cut(position["value"], 2)

            token_id = position["tokenId"]

            action_name = "Selling"

        else:
            raise Exception(f'Unsupported order_side: `{order_side}`')

        book = await self.browser.get_event_book(
            question_id=event["raw_event"]["questionId"],
            symbol=event["raw_event"]["yesPos" if event_choice_index == 0 else "noPos"],
            event_choice_index=event_choice_index,
        )
        if order_type == "market":
            price = book["asks" if order_side == "buy" else "bids"][0]
            taker_amount = 0

        elif order_type == "limit":
            price = self._calculate_limit_price(order_side, book)

            if order_side == "buy":
                taker_amount = float(round_cut(amount / price, 2))
                amount = float(Decimal(str(taker_amount)) * Decimal(str(price)))
            else:
                taker_amount = float(Decimal(str(amount)) * Decimal(str(price)))

        else:
            raise CustomError(f'Unsupported order type `{order_type}`')

        typed_data = self.TYPED_DATA.copy()
        typed_data["message"].update({
            "salt": str(int(random() * int(time() * 1e3))),
            "maker": self.proxy_wallet,
            "signer": self.wallet.address,
            "tokenId": token_id,
            "makerAmount": str(int(Decimal(str(amount)) * Decimal('1e18'))),
            "takerAmount": str(int(Decimal(str(taker_amount)) * Decimal('1e18'))),
            "side": str(side),
        })
        signature = self.wallet.sign_message(typed_data=typed_data)

        self.log_message(
            f'{action_name} <green>{usd_amount} USDT</green> for {label} in <blue>{event["name"]}</blue> <green>at {round(price * 100, 2)}¢</green>',
            level="INFO"
        )
        order_data = await self.browser.create_order(
            typed_message=typed_data["message"],
            signature=signature,
            event_id=event["raw_event"]["topicId"],
            safe_rate="0" if (order_side == "buy" and order_type == "market") else "0.05",
            price=str(price) if order_type == "limit" else "0"
        )

        if order_type == "limit":
            to_wait_sec = LIMIT_SETTINGS[f"to_wait_{order_side}"] * 60
            deadline_ts = int(time()) + to_wait_sec
            minutes_str = f"{LIMIT_SETTINGS[f'to_wait_{order_side}']} minute{'s' if LIMIT_SETTINGS[f'to_wait_{order_side}'] > 1 else ''}"
        else:
            minutes_str = ""

        self.log_message(f"Waiting for {order_type} {order_side} order filled" + (f" {minutes_str}" if minutes_str else ""))
        while True:
            filled_order = await self.browser.get_orders(
                topic_id=event["raw_event"]["topicId"],
                trans_no=order_data["transNo"],
                is_parent=event["is_child"],
                order_type=order_type,
            )
            if filled_order is None:
                raise Exception(f'Failed to found order {order_data["transNo"]}')

            if round(float(filled_order["filled"].split('/')[0]), 2)  == round(float(filled_order["filled"].split('/')[1]), 2):
                final_price = round(float(filled_order["price"]) * 100, 2)
                total_price = round_cut(filled_order["totalPrice"], 2)
                self.log_message(f"Filled {order_type} {order_side} order for <green>{total_price}$ at {final_price}¢</green>", level="INFO")
                await self.wallet.db.append_report(
                    encoded_pk=self.wallet.encoded_pk,
                    text=f"{order_type} {order_side} «{label}» for {usd_amount}$ at {final_price}¢ in {event['name']}",
                    success=True
                )
                break

            elif order_type == "limit":
                if time() > deadline_ts:
                    book = await self.browser.get_event_book(
                        question_id=event["raw_event"]["questionId"],
                        symbol=event["raw_event"]["yesPos" if event_choice_index == 0 else "noPos"],
                        event_choice_index=event_choice_index,
                    )
                    if price == self._calculate_limit_price(order_side, book):
                        self.log_message(f"Limit order not filled in {minutes_str}, but price not changed, waiting again...")
                        deadline_ts = int(time()) + to_wait_sec
                    else:
                        self.log_message(f"Limit order not filled in {minutes_str}, changing price...")

                        await self.browser.cancel_order(order_data["transNo"])
                        self.log_message(f'Cancelled order in "{event["name"]}"', level="INFO")

                        if order_side == "buy":
                            event["force_vote"] = event_choice_index + 1

                        return await self.create_order(
                                order_side=order_side,
                                order_type=order_type,
                                event=event,
                                order=order,
                                position=position,
                        )

            await async_sleep(3)

        return {
            "order": filled_order,
            "event": event,
        }


    async def get_balance(self):
        profile_info = await self.browser.get_profile_info()
        return float(profile_info["balance"][0]["balance"])


    async def calculate_order_amount(self):
        balance = await self.get_balance()
        if BID_SETTINGS["AMOUNTS"]["amounts"] != [0, 0]:
            amounts = BID_SETTINGS["AMOUNTS"]["amounts"][:]
            if amounts[0] > balance:
                raise Exception(f'Not enough balance: need {amounts[0]} have {round(balance, 2)}')
            elif amounts[1] > balance:
                amounts[1] = balance
            amount = uniform(*amounts)
        else:
            percent = uniform(*BID_SETTINGS["AMOUNTS"]["percents"]) / 100
            amount = balance * percent

        return round_cut(amount, 2)


    @classmethod
    def _calculate_limit_price(cls, order_side: str, book: dict):
        price_diff = float(round_cut(LIMIT_SETTINGS[f"diff_price_{order_side}"] / 100, 3))
        if order_side == "sell":
            price_diff *= -1
        price = float(round_cut(book["bids" if order_side == "buy" else "asks"][0] - price_diff, 3))
        return price


    def log_message(
            self,
            text: str,
            smile: str = "•",
            level: str = "DEBUG",
            colors: bool = True
    ):
        label = f"<white>{self.wallet.address}</white>" if colors else self.wallet.address
        logger.opt(colors=colors).log(level.upper(), f'[{smile}] {label} | {text}')

