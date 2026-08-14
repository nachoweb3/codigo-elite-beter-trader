"""
Cliente WebSocket para monitoreo en tiempo real de transacciones
Usa Helius WebSocket subscriptions para streaming de datos
"""
import asyncio
import json
from typing import Dict, Callable, Optional, Any
from datetime import datetime
import httpx
from app.config import get_settings

settings = get_settings()


class HeliusWebSocket:
    """Cliente WebSocket para Helius"""

    def __init__(self):
        self.api_key = settings.helius_api_key
        self.ws_url = settings.helius_ws_url
        self.ws = None
        self.subscribed_addresses = set()
        self.callbacks = {
            "transaction": [],
            "account": [],
            "token": []
        }

    async def connect(self):
        """Establece conexión WebSocket"""
        import websockets
        self.ws = await websockets.connect(self.ws_url)
        return self.ws

    async def disconnect(self):
        """Cierra conexión WebSocket"""
        if self.ws:
            await self.ws.close()

    def on_transaction(self, callback: Callable):
        """Registra callback para nuevas transacciones"""
        self.callbacks["transaction"].append(callback)

    async def subscribe_account(self, address: str):
        """Suscribe a transacciones de una cuenta"""
        if address in self.subscribed_addresses:
            return

        self.subscribed_addresses.add(address)

        subscription = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "transactionSubscribe",
            "params": [
                {
                    "accountIncludes": [address],
                    "failed": False
                },
                {
                    "encoding": "json",
                    "transactionDetails": "full",
                    "maxSupportedTransactionVersion": 0
                }
            ]
        }

        if self.ws:
            await self.ws.send(json.dumps(subscription))

    async def subscribe_token_transfers(self, mint: str):
        """Suscribe a transferencias de un token específico"""
        subscription = {
            "jsonrpc": "2.0",
            "id": len(self.subscribed_addresses) + 1,
            "method": "transactionSubscribe",
            "params": [
                {
                    "tokenMint": mint,
                    "failed": False
                },
                {
                    "encoding": "json",
                    "transactionDetails": "full",
                    "maxSupportedTransactionVersion": 0
                }
            ]
        }

        if self.ws:
            await self.ws.send(json.dumps(subscription))

    async def listen(self):
        """Escucha mensajes del WebSocket"""
        if not self.ws:
            await self.connect()

        try:
            async for message in self.ws:
                data = json.loads(message)

                # Procesar notificación de transacción
                if "params" in data and "result" in data["params"]:
                    result = data["params"]["result"]

                    if "value" in result and result["value"]:
                        transaction = result["value"]

                        # Extraer datos relevantes
                        parsed_tx = await self._parse_transaction(transaction)

                        # Ejecutar callbacks
                        for callback in self.callbacks["transaction"]:
                            await callback(parsed_tx)

        except Exception as e:
            print(f"Error en WebSocket: {e}")
            # Reintentar conexión
            await asyncio.sleep(5)
            await self.connect()

    async def _parse_transaction(self, tx_data: Dict) -> Optional[Dict]:
        """Parsea una transacción del WebSocket"""
        try:
            meta = tx_data.get("meta", {})
            transaction = tx_data.get("transaction", {})
            message = transaction.get("message", {})

            # Obtener signature
            signatures = tx_data.get("signatures", [])
            signature = signatures[0] if signatures else ""

            # Obtener timestamp
            slot = tx_data.get("slot", 0)

            # Obtener pre/post balances para detectar cambios
            pre_balances = meta.get("preBalances", [])
            post_balances = meta.get("postBalances", [])

            # Obtener accountKeys
            account_keys = [acc["pubkey"] for acc in message.get("accountKeys", [])]

            # Detectar transferencias de token
            token_balances = meta.get("postTokenBalances", [])
            pre_token_balances = meta.get("preTokenBalances", [])

            changes = []

            for i, post_balance in enumerate(token_balances):
                mint = post_balance.get("mint", "")
                account_index = post_balance.get("accountIndex")

                # Obtener balance anterior
                pre_balance = None
                for pb in pre_token_balances:
                    if pb.get("accountIndex") == account_index:
                        pre_balance = pb
                        break

                if pre_balance:
                    pre_amount = float(pre_balance.get("uiTokenAmount", {}).get("amount", 0))
                    post_amount = float(post_balance.get("uiTokenAmount", {}).get("amount", 0))

                    if pre_amount != post_amount:
                        changes.append({
                            "mint": mint,
                            "account_index": account_index,
                            "change": post_amount - pre_amount,
                            "balance": post_amount
                        })

            # Detectar cambios en SOL
            sol_changes = []
            for i, (pre, post) in enumerate(zip(pre_balances, post_balances)):
                change = (post - pre) / 1e9  # Convertir lamports a SOL
                if abs(change) > 0.000001:  # Cambio mínimo significativo
                    sol_changes.append({
                        "account": account_keys[i],
                        "change": change,
                        "balance": post / 1e9
                    })

            return {
                "signature": signature,
                "slot": slot,
                "timestamp": datetime.now().isoformat(),
                "sol_changes": sol_changes,
                "token_changes": changes,
                "fee": meta.get("fee", 0) / 1e9,
                "status": "success" if meta.get("err") is None else "failed"
            }

        except Exception as e:
            print(f"Error parseando transacción: {e}")
            return None


class MoneyFlowAnalyzer:
    """Analiza flujos de dinero en tiempo real"""

    def __init__(self):
        self.flows = []  # Historial de flujos
        self.current_positions = {}  # Posiciones actuales por token
        self.token_sentiment = {}  # Sentimiento por token (accumulation/distribution)

    def analyze_flow(self, transaction: Dict, wallet_address: str) -> Dict:
        """Analiza una transacción y determina el flujo de dinero"""

        flow = {
            "timestamp": transaction.get("timestamp"),
            "signature": transaction.get("signature"),
            "type": None,  # "buy", "sell", "transfer_in", "transfer_out"
            "token": None,
            "sol_amount": 0,
            "token_amount": 0,
            "reason": "",
            "sentiment": "neutral",
            "price_impact": 0
        }

        sol_changes = transaction.get("sol_changes", [])
        token_changes = transaction.get("token_changes", [])

        # Buscar cambios en la wallet del usuario
        wallet_sol_change = 0
        for change in sol_changes:
            if change.get("account") == wallet_address:
                wallet_sol_change = change.get("change", 0)
                break

        # Analizar cambios de tokens
        for change in token_changes:
            mint = change.get("mint")
            amount_change = change.get("change", 0)

            if amount_change > 0:
                # Token entrando (compra o recepción)
                if wallet_sol_change < 0:
                    flow["type"] = "buy"
                    flow["sentiment"] = "accumulation"
                    flow["reason"] = f"Invirtiendo {abs(wallet_sol_change):.4f} SOL en {mint}"
                    flow["sol_amount"] = abs(wallet_sol_change)
                    flow["token"] = mint
                    flow["token_amount"] = amount_change

                    # Actualizar sentimiento del token
                    self._update_token_sentiment(mint, "accumulation", abs(wallet_sol_change))

                else:
                    flow["type"] = "transfer_in"
                    flow["reason"] = f"Recibiendo {amount_change} tokens"

            elif amount_change < 0:
                # Token saliendo (venta o envío)
                if wallet_sol_change > 0:
                    flow["type"] = "sell"
                    flow["sentiment"] = "distribution"
                    flow["reason"] = f"Vende {abs(amount_change)} tokens por {wallet_sol_change:.4f} SOL"
                    flow["sol_amount"] = wallet_sol_change
                    flow["token"] = mint
                    flow["token_amount"] = abs(amount_change)

                    # Actualizar sentimiento del token
                    self._update_token_sentiment(mint, "distribution", wallet_sol_change)

                else:
                    flow["type"] = "transfer_out"
                    flow["reason"] = f"Enviando {abs(amount_change)} tokens"

        # Actualizar posiciones actuales
        if flow["token"] and flow["type"] in ["buy", "sell"]:
            self._update_position(flow["token"], flow["type"], flow["sol_amount"])

        # Guardar en historial
        self.flows.append(flow)

        # Generar insights
        flow["insights"] = self._generate_insights()

        return flow

    def _update_token_sentiment(self, mint: str, action: str, amount: float):
        """Actualiza el sentimiento de un token"""
        if mint not in self.token_sentiment:
            self.token_sentiment[mint] = {
                "accumulation": 0,
                "distribution": 0,
                "sentiment": "neutral"
            }

        self.token_sentiment[mint][action] += amount

        # Calcular ratio acumulación/distribución
        total = self.token_sentiment[mint]["accumulation"] + self.token_sentiment[mint]["distribution"]

        if total > 0:
            accum_ratio = self.token_sentiment[mint]["accumulation"] / total
            if accum_ratio > 0.6:
                self.token_sentiment[mint]["sentiment"] = "strong_accumulation"
            elif accum_ratio > 0.4:
                self.token_sentiment[mint]["sentiment"] = "accumulation"
            elif accum_ratio > 0.2:
                self.token_sentiment[mint]["sentiment"] = "distribution"
            else:
                self.token_sentiment[mint]["sentiment"] = "strong_distribution"

    def _update_position(self, token: str, action: str, amount: float):
        """Actualiza las posiciones actuales"""
        if token not in self.current_positions:
            self.current_positions[token] = {
                "invested": 0,
                "trades": 0
            }

        self.current_positions[token]["trades"] += 1

        if action == "buy":
            self.current_positions[token]["invested"] += amount
        elif action == "sell":
            self.current_positions[token]["invested"] -= amount

    def _generate_insights(self) -> list:
        """Genera insights basados en los patrones detectados"""
        insights = []

        # Detectar tokens con alta acumulación
        for mint, data in self.token_sentiment.items():
            if data["sentiment"] == "strong_accumulation":
                invested = self.current_positions.get(mint, {}).get("invested", 0)
                if invested > 0:
                    insights.append({
                        "type": "accumulation",
                        "token": mint,
                        "message": f"Fuerte acumulación en {mint[:8]}... (${invested:.2f} SOL invertidos)",
                        "suggestion": "Considerar mantener posición - patrón de acumulación detectado"
                    })
            elif data["sentiment"] == "strong_distribution":
                insights.append({
                    "type": "distribution",
                    "token": mint,
                    "message": f"Distribución activa de {mint[:8]}...",
                    "suggestion": "Tomando ganancias o saliendo de posición"
                })

        # Detectar concentración de riesgo
        if self.current_positions:
            total_invested = sum(pos.get("invested", 0) for pos in self.current_positions.values())
            if total_invested > 0:
                for mint, pos in self.current_positions.items():
                    concentration = pos.get("invested", 0) / total_invested
                    if concentration > 0.5:
                        insights.append({
                            "type": "warning",
                            "token": mint,
                            "message": f"Alta concentración en {mint[:8]}... ({concentration*100:.1f}%)",
                            "suggestion": "Considerar diversificar para reducir riesgo"
                        })

        return insights

    def get_flow_summary(self) -> Dict:
        """Obtiene un resumen de los flujos de dinero"""
        if not self.flows:
            return {
                "total_buys": 0,
                "total_sells": 0,
                "net_flow": 0,
                "active_positions": len(self.current_positions),
                "trending_tokens": []
            }

        buys = [f for f in self.flows if f["type"] == "buy"]
        sells = [f for f in self.flows if f["type"] == "sell"]

        total_invested = sum(f.get("sol_amount", 0) for f in buys)
        total_withdrawn = sum(f.get("sol_amount", 0) for f in sells)

        # Tokens trending
        token_volume = {}
        for flow in self.flows[-20:]:  # Últimas 20 transacciones
            token = flow.get("token")
            if token:
                token_volume[token] = token_volume.get(token, 0) + flow.get("sol_amount", 0)

        trending = sorted(token_volume.items(), key=lambda x: x[1], reverse=True)[:5]

        return {
            "total_buys": len(buys),
            "total_sells": len(sells),
            "total_invested": total_invested,
            "total_withdrawn": total_withdrawn,
            "net_flow": total_invested - total_withdrawn,
            "active_positions": len(self.current_positions),
            "current_positions": dict(self.current_positions),
            "token_sentiment": dict(self.token_sentiment),
            "trending_tokens": [{"mint": m, "volume": v} for m, v in trending]
        }

    def get_recommendations(self) -> list:
        """Genera recomendaciones basadas en el análisis de flujo"""
        recommendations = []
        summary = self.get_flow_summary()

        # Análisis de flujo neto
        if summary["net_flow"] > 5:
            recommendations.append({
                "priority": "info",
                "title": "Alta actividad de compra",
                "description": f"Hay {summary['net_flow']:.2f} SOL más entrando que saliendo. Patrón de acumulación activo.",
                "actionable": True
            })
        elif summary["net_flow"] < -5:
            recommendations.append({
                "priority": "warning",
                "title": "Alta actividad de venta",
                "description": f"Hay {abs(summary['net_flow']):.2f} SOL más saliendo que entrando. Considera revisar tu estrategia.",
                "actionable": True
            })

        # Análisis por token
        for mint, sentiment in summary["token_sentiment"].items():
            if sentiment["sentiment"] == "strong_accumulation":
                recommendations.append({
                    "priority": "success",
                    "title": f"Acumulación detectada: {mint[:8]}...",
                    "description": f"Has acumulado significativamente este token. Patrón positivo.",
                    "actionable": False
                })
            elif sentiment["sentiment"] == "strong_distribution":
                recommendations.append({
                    "priority": "warning",
                    "title": f"Distribución: {mint[:8]}...",
                    "description": "Estás reduciendo posición en este token.",
                    "actionable": False
                })

        return recommendations


# Instancia global del analizador
flow_analyzer = MoneyFlowAnalyzer()
