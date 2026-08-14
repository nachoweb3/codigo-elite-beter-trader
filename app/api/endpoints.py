from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from datetime import datetime
from app.models.schemas import (
    WalletRequest, AnalysisRequest, WalletAnalysis, PortfolioResponse,
    TradingMetrics, TokenStats, Recommendation, PatternAnalysis, Transaction
)
from app.services.prices import get_token_price_sol
from app.blockchain.rpc_client import SolanaRPCClient
from app.services.helius_parser import HeliusAPI
from app.services.trading_analyzer import TradingAnalyzer
from app.services.market import get_sol_price_usd
from app.services.feedback import feedback_store
from app.services.community import community_store
from app.services.cache import TTLCache
from app.blockchain.websocket_client import flow_analyzer
from app.config import get_settings

settings = get_settings()
router = APIRouter(prefix="/api", tags=["analysis"])

# Almacenamiento de sesiones activas para streaming
active_sessions = {}

# Caché de análisis por wallet (TTL 45s): con una comunidad de hasta 100
# personas, varios usuarios pueden analizar la misma wallet a la vez. Sin
# caché, cada análisis repetido volvía a golpear Helius (rate limit). El TTL
# coincide con el ciclo de refresh en vivo del frontend (45s), así que los
# precios nunca se quedan más viejos de lo que ya mostraba el dashboard.
analysis_cache = TTLCache(default_ttl=45)


async def _enrich_tokens_with_live_prices(tokens: list) -> list:
    """Añade precio actual y PnL no realizado a cada token con posición abierta.

    Para tokens que aún se mantienen (is_still_holding), consulta el precio
    en tiempo real (DexScreener/GeckoTerminal) y calcula:
    - unrealized_pnl_sol: (precio_actual - precio_compra) * holdings
    - unrealized_roi_percent: % de ganancia/pérdida sobre lo invertido
    """
    enriched = []
    for token in tokens:
        item = token
        if token.is_still_holding and token.current_holdings and token.current_holdings > 0:
            current_price = await get_token_price_sol(token.token_address)
            if current_price and current_price > 0 and token.avg_buy_price > 0:
                unrealized = (current_price - token.avg_buy_price) * token.current_holdings
                # PnL no realizado en SOL
                total_invested = token.avg_buy_price * token.current_holdings
                roi = (unrealized / total_invested * 100) if total_invested > 0 else 0
                item = TokenStats(
                    **{**token.model_dump(),
                       "current_price_sol": round(current_price, 12),
                       "unrealized_pnl_sol": round(unrealized, 6),
                       "unrealized_roi_percent": round(roi, 2)}
                )
        enriched.append(item)
    return enriched


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "CE BetterTrader API"}


@router.get("/wallet/balance")
async def get_wallet_balance(wallet_address: str = Query(..., description="Solana wallet address")):
    """Obtiene el balance de SOL de una wallet"""
    if not SolanaRPCClient.is_valid_address(wallet_address):
        raise HTTPException(status_code=400, detail="Invalid wallet address")

    client = SolanaRPCClient()
    try:
        balance = await client.get_balance(wallet_address)
        return {"wallet": wallet_address, "balance": balance}
    finally:
        await client.close()


@router.post("/wallet/analyze", response_model=WalletAnalysis)
async def analyze_wallet(request: AnalysisRequest):
    """
    Analiza una wallet de Solana y genera métricas de trading
    Usa la API de Helius para obtener transacciones de swaps
    """
    wallet_address = request.wallet_address

    if not SolanaRPCClient.is_valid_address(wallet_address):
        raise HTTPException(status_code=400, detail="Invalid wallet address")

    # Si ya hay un análisis reciente (≤45s) de esta wallet, devolverlo sin
    # volver a golpear Helius — clave para 100 usuarios analizando a la vez.
    cached = analysis_cache.get(wallet_address)
    if cached is not None:
        return cached

    # Inicializar clientes
    rpc_client = SolanaRPCClient()
    helius_api = HeliusAPI()

    try:
        # Obtener balance de SOL
        balance = await rpc_client.get_balance(wallet_address)

        # Obtener transacciones desde Helius API REST
        raw_transactions = await helius_api.get_transactions(
            wallet_address,
            limit=min(request.limit, 100)
        )

        print(f"Transacciones obtenidas: {len(raw_transactions)}")

        # Filtrar solo swaps y parsear
        transactions = []
        swap_count = 0

        for tx_data in raw_transactions:
            tx_type = tx_data.get("type", "")

            # Procesar swaps y transfers con tokenTransfers (algunas ventas
            # de agregadores vienen clasificadas como TRANSFER)
            if tx_type in ("SWAP", "TRANSFER"):
                swap_count += 1
                parsed = await helius_api.parse_swap_transaction(tx_data, wallet_address)
                transactions.extend(parsed)

        print(f"Swaps detectados: {swap_count}")
        print(f"Transacciones parseadas: {len(transactions)}")

        # Si no hay transacciones de trading
        if not transactions:
            empty = WalletAnalysis(
                wallet_address=wallet_address,
                analyzed_at=datetime.now(),
                balance_sol=balance,
                metrics=TradingMetrics(
                    total_trades=0, winning_trades=0, losing_trades=0, win_rate=0,
                    total_pnl=0, total_volume=0, total_fees=0, avg_trade_size=0,
                    avg_hold_time_seconds=0, largest_win=0, largest_loss=0, profit_factor=0
                ),
                tokens=[],
                recommendations=[
                    Recommendation(
                        type="info",
                        priority="low",
                        title="No se detectaron operaciones de trading",
                        description="No se encontraron transacciones de swap en esta wallet. Si has hecho trades, verifica que la dirección sea correcta.",
                        actionable=False
                    )
                ],
                patterns=[],
                transactions=[]
            )
            analysis_cache.set(wallet_address, empty)
            return empty

        # Analizar transacciones
        analyzer = TradingAnalyzer(transactions)
        metrics = analyzer.calculate_metrics()
        tokens = analyzer.analyze_all_tokens()
        patterns = analyzer.analyze_patterns()
        recommendations = analyzer.generate_recommendations()
        profile = analyzer.detect_profile()
        time_of_day = analyzer.analyze_time_of_day()
        score = analyzer.calculate_trading_score()

        # PnL no realizado EN TIEMPO REAL: para cada token con posición abierta,
        # consultar el precio actual (DexScreener/GeckoTerminal, sin key) y
        # calcular cuánto se está ganando/perdiendo ahora mismo.
        from app.services.prices import get_token_price_sol
        tokens = await _enrich_tokens_with_live_prices(tokens)

        # P&L total = realizado + no realizado (posiciones abiertas)
        unrealized_total = sum(t.unrealized_pnl_sol or 0 for t in tokens)
        metrics_updated = metrics
        if unrealized_total:
            metrics_updated = TradingMetrics(
                **{**metrics.model_dump(), "total_pnl": round(metrics.total_pnl + unrealized_total, 6)}
            )

        # Snapshot de comunidad: guarda esta wallet (anónima) para que el
        # usuario vea sus percentiles frente al resto de la comunidad.
        try:
            community_store.record_analysis(
                wallet_address,
                metrics_updated.model_dump(),
                profile=profile.model_dump() if profile else None,
                tokens_count=len(tokens),
            )
        except Exception as e:
            print(f"Error registrando snapshot de comunidad: {e}")

        result = WalletAnalysis(
            wallet_address=wallet_address,
            analyzed_at=datetime.now(),
            balance_sol=balance,
            metrics=metrics_updated,
            tokens=tokens[:20],
            recommendations=recommendations[:10],
            patterns=patterns,
            transactions=transactions[:50],
            profile=profile,
            time_of_day=time_of_day,
            score=score
        )
        analysis_cache.set(wallet_address, result)
        return result

    except Exception as e:
        print(f"Error en análisis: {e}")
        raise HTTPException(status_code=500, detail=f"Error analyzing wallet: {str(e)}")

    finally:
        await rpc_client.close()
        await helius_api.close()


@router.get("/wallet/transactions")
async def get_wallet_transactions(
    wallet_address: str = Query(..., description="Solana wallet address"),
    limit: int = Query(20, description="Number of transactions", ge=1, le=100)
):
    """Obtiene las últimas transacciones de swaps de una wallet"""
    if not SolanaRPCClient.is_valid_address(wallet_address):
        raise HTTPException(status_code=400, detail="Invalid wallet address")

    helius_api = HeliusAPI()

    try:
        raw_transactions = await helius_api.get_transactions(wallet_address, limit=limit)

        transactions = []
        for tx_data in raw_transactions:
            if tx_data.get("type") in ("SWAP", "TRANSFER"):
                parsed = await helius_api.parse_swap_transaction(tx_data, wallet_address)
                transactions.extend(parsed)

            if len(transactions) >= limit:
                break

        return {"wallet": wallet_address, "transactions": transactions}

    finally:
        await helius_api.close()


@router.get("/market/sol-price")
async def get_sol_price():
    """Obtiene el precio actual de SOL en USD (con caché y fallback)"""
    try:
        price = await get_sol_price_usd()
        return {"solana": {"usd": price}}
    except Exception:
        return {"solana": {"usd": 0}}


@router.get("/wallet/flow-summary")
async def get_flow_summary(wallet_address: str = Query(..., description="Solana wallet address")):
    """Obtiene el resumen de flujo de dinero de una wallet"""
    if not SolanaRPCClient.is_valid_address(wallet_address):
        raise HTTPException(status_code=400, detail="Invalid wallet address")

    # Analizar transacciones históricas para construir el flujo
    helius_api = HeliusAPI()

    try:
        raw_transactions = await helius_api.get_transactions(wallet_address, limit=100)

        # Procesar transacciones para calcular flujo
        flow_summary = {
            "wallet": wallet_address,
            "total_inflow": 0,  # SOL entrando por ventas
            "total_outflow": 0,  # SOL saliendo por compras
            "net_flow": 0,
            "transaction_count": 0,
            "buy_count": 0,
            "sell_count": 0,
            "token_flows": {},  # Flujo por token
            "recent_activity": [],
            "insights": []
        }

        for tx_data in raw_transactions:
            if tx_data.get("type") in ("SWAP", "TRANSFER"):
                parsed = await helius_api.parse_swap_transaction(tx_data, wallet_address)

                for tx in parsed:
                    flow_summary["transaction_count"] += 1

                    if tx.type.value == "buy":
                        flow_summary["buy_count"] += 1
                        flow_summary["total_outflow"] += tx.sol_amount

                        token = tx.token_address
                        if token not in flow_summary["token_flows"]:
                            flow_summary["token_flows"][token] = {
                                "symbol": tx.token_symbol,
                                "invested": 0,
                                "withdrawn": 0,
                                "trades": 0,
                                "sentiment": "neutral"
                            }

                        flow_summary["token_flows"][token]["invested"] += tx.sol_amount
                        flow_summary["token_flows"][token]["trades"] += 1

                        # Actividad reciente
                        flow_summary["recent_activity"].append({
                            "timestamp": tx.timestamp.isoformat(),
                            "type": "buy",
                            "token": tx.token_symbol,
                            "sol_amount": tx.sol_amount,
                            "token_amount": tx.token_amount,
                            "reason": f"Compra de {tx.token_symbol} por {tx.sol_amount:.4f} SOL"
                        })

                    elif tx.type.value == "sell":
                        flow_summary["sell_count"] += 1
                        flow_summary["total_inflow"] += tx.sol_amount

                        token = tx.token_address
                        if token not in flow_summary["token_flows"]:
                            flow_summary["token_flows"][token] = {
                                "symbol": tx.token_symbol,
                                "invested": 0,
                                "withdrawn": 0,
                                "trades": 0,
                                "sentiment": "neutral"
                            }

                        flow_summary["token_flows"][token]["withdrawn"] += tx.sol_amount
                        flow_summary["token_flows"][token]["trades"] += 1

                        # Actividad reciente
                        flow_summary["recent_activity"].append({
                            "timestamp": tx.timestamp.isoformat(),
                            "type": "sell",
                            "token": tx.token_symbol,
                            "sol_amount": tx.sol_amount,
                            "token_amount": tx.token_amount,
                            "reason": f"Venta de {tx.token_symbol} por {tx.sol_amount:.4f} SOL"
                        })

        # Calcular flujo neto
        flow_summary["net_flow"] = flow_summary["total_inflow"] - flow_summary["total_outflow"]

        # Calcular sentimiento por token
        for token, data in flow_summary["token_flows"].items():
            net = data["withdrawn"] - data["invested"]
            total = data["invested"] + data["withdrawn"]

            if total > 0:
                if net > 0:
                    data["pnl"] = net
                    if net > total * 0.2:
                        data["sentiment"] = "profitable"
                    else:
                        data["sentiment"] = "gaining"
                else:
                    data["pnl"] = net
                    if net < -total * 0.2:
                        data["sentiment"] = "losing"
                    else:
                        data["sentiment"] = "accumulating"
            else:
                data["pnl"] = 0

        # Generar insights
        if flow_summary["net_flow"] > 5:
            flow_summary["insights"].append({
                "type": "accumulation",
                "title": "Patrón de Acumulación",
                "description": f"Hay {flow_summary['net_flow']:.2f} SOL más saliendo que entrando. Estás invirtiendo activamente.",
                "priority": "info"
            })
        elif flow_summary["net_flow"] < -5:
            flow_summary["insights"].append({
                "type": "distribution",
                "title": "Patrón de Distribución",
                "description": f"Hay {abs(flow_summary['net_flow']):.2f} SOL más entrando que saliendo. Estás tomando ganancias.",
                "priority": "success"
            })

        # Buscar tokens más activos
        sorted_tokens = sorted(
            flow_summary["token_flows"].items(),
            key=lambda x: x[1]["trades"],
            reverse=True
        )[:5]

        for token, data in sorted_tokens:
            if data["sentiment"] == "profitable":
                flow_summary["insights"].append({
                    "type": "success",
                    "title": f"Token Rentable: {data['symbol']}",
                    "description": f"Has generado {data['pnl']:.4f} SOL de ganancia en {data['symbol']}.",
                    "priority": "high"
                })
            elif data["sentiment"] == "losing" and data["trades"] > 2:
                flow_summary["insights"].append({
                    "type": "warning",
                    "title": f"Token con Pérdidas: {data['symbol']}",
                    "description": f"Tienes {data['pnl']:.4f} SOL de pérdida en {data['symbol']}. Considera revisar tu estrategia.",
                    "priority": "medium"
                })

        # Limitar actividad reciente
        flow_summary["recent_activity"] = flow_summary["recent_activity"][:15]

        return flow_summary

    finally:
        await helius_api.close()


@router.post("/wallet/simulate-stream")
async def simulate_stream(request: AnalysisRequest):
    """
    Simula un stream de transacciones en tiempo real
    Analiza las últimas transacciones y las presenta como stream
    """
    wallet_address = request.wallet_address

    if not SolanaRPCClient.is_valid_address(wallet_address):
        raise HTTPException(status_code=400, detail="Invalid wallet address")

    helius_api = HeliusAPI()

    try:
        raw_transactions = await helius_api.get_transactions(wallet_address, limit=50)

        stream_events = []

        for tx_data in raw_transactions:
            if tx_data.get("type") in ("SWAP", "TRANSFER"):
                parsed = await helius_api.parse_swap_transaction(tx_data, wallet_address)

                for tx in parsed:
                    # Crear evento de stream
                    event = {
                        "id": f"{tx.signature}_{tx.token_address}",
                        "timestamp": tx.timestamp.isoformat(),
                        "type": tx.type.value,
                        "data": {
                            "token_address": tx.token_address,
                            "token_symbol": tx.token_symbol,
                            "token_amount": tx.token_amount,
                            "sol_amount": tx.sol_amount,
                            "price": tx.price_per_token,
                            "fee": tx.fee,
                            "signature": tx.signature
                        },
                        "analysis": {}
                    }

                    # Análisis del evento
                    if tx.type.value == "buy":
                        event["analysis"] = {
                            "flow_direction": "out",
                            "reason": f"Invirtiendo {tx.sol_amount:.4f} SOL en {tx.token_symbol}",
                            "sentiment": "accumulation",
                            "what_happening": f"Dinero fluye hacia {tx.token_symbol} - Posición de apertura o incremento",
                            "suggestion": "Monitorizar precio para tomar ganancias en punto óptimo"
                        }
                    elif tx.type.value == "sell":
                        event["analysis"] = {
                            "flow_direction": "in",
                            "reason": f"Venta de {tx.token_symbol} por {tx.sol_amount:.4f} SOL",
                            "sentiment": "distribution",
                            "what_happening": f"Dinero regresa desde {tx.token_symbol} - Cierre de posición o toma de ganancias",
                            "suggestion": "Capital disponible para nuevas oportunidades"
                        }

                    stream_events.append(event)

        # Ordenar por timestamp (más reciente primero)
        stream_events.sort(key=lambda x: x["timestamp"], reverse=True)

        return {
            "wallet": wallet_address,
            "events": stream_events[:30],
            "summary": {
                "total_events": len(stream_events),
                "buy_events": sum(1 for e in stream_events if e["type"] == "buy"),
                "sell_events": sum(1 for e in stream_events if e["type"] == "sell"),
                "total_sol_out": sum(e["data"]["sol_amount"] for e in stream_events if e["type"] == "buy"),
                "total_sol_in": sum(e["data"]["sol_amount"] for e in stream_events if e["type"] == "sell")
            }
        }

    finally:
        await helius_api.close()


@router.get("/wallet/flow-directions")
async def get_flow_directions(wallet_address: str = Query(..., description="Solana wallet address")):
    """
    Obtiene las direcciones de flujo de dinero con explicaciones
    """
    if not SolanaRPCClient.is_valid_address(wallet_address):
        raise HTTPException(status_code=400, detail="Invalid wallet address")

    helius_api = HeliusAPI()

    try:
        raw_transactions = await helius_api.get_transactions(wallet_address, limit=100)

        # Construir mapa de flujos
        flows = {
            "wallet": wallet_address,
            "sol_flows": {
                "outgoing": [],  # SOL saliendo (compras)
                "incoming": [],  # SOL entrando (ventas)
                "net": 0
            },
            "token_flows": [],
            "recommendations": []
        }

        token_tracking = {}  # Para calcular ROI por token

        for tx_data in raw_transactions:
            if tx_data.get("type") in ("SWAP", "TRANSFER"):
                parsed = await helius_api.parse_swap_transaction(tx_data, wallet_address)

                for tx in parsed:
                    flow_item = {
                        "timestamp": tx.timestamp.isoformat(),
                        "token_symbol": tx.token_symbol,
                        "token_address": tx.token_address,
                        "amount": tx.sol_amount,
                        "token_amount": tx.token_amount,
                        "signature": tx.signature
                    }

                    if tx.type.value == "buy":
                        # SOL sale (compra de token)
                        flows["sol_flows"]["outgoing"].append(flow_item)

                        # Track para ROI
                        if tx.token_address not in token_tracking:
                            token_tracking[tx.token_address] = {
                                "symbol": tx.token_symbol,
                                "invested": 0,
                                "recovered": 0,
                                "buys": 0,
                                "sells": 0
                            }

                        token_tracking[tx.token_address]["invested"] += tx.sol_amount
                        token_tracking[tx.token_address]["buys"] += 1

                    elif tx.type.value == "sell":
                        # SOL entra (venta de token)
                        flows["sol_flows"]["incoming"].append(flow_item)

                        # Track para ROI
                        if tx.token_address not in token_tracking:
                            token_tracking[tx.token_address] = {
                                "symbol": tx.token_symbol,
                                "invested": 0,
                                "recovered": 0,
                                "buys": 0,
                                "sells": 0
                            }

                        token_tracking[tx.token_address]["recovered"] += tx.sol_amount
                        token_tracking[tx.token_address]["sells"] += 1

        # Calcular net flow
        total_out = sum(f["amount"] for f in flows["sol_flows"]["outgoing"])
        total_in = sum(f["amount"] for f in flows["sol_flows"]["incoming"])
        flows["sol_flows"]["net"] = total_in - total_out

        # Construir flujos de token con ROI
        for token_addr, data in token_tracking.items():
            if data["buys"] > 0 or data["sells"] > 0:
                # Si no se ha vendido nada, la posición sigue abierta:
                # no hay P&L realizado ni ROI (no es una pérdida del -100%).
                if data["sells"] == 0:
                    pnl = 0.0
                    roi = 0.0
                else:
                    pnl = data["recovered"] - data["invested"]
                    roi = (pnl / data["invested"] * 100) if data["invested"] > 0 else 0

                # Logo del token (usa la caché del servicio de metadatos)
                token_info = await helius_api.get_token_info(token_addr)

                token_flow = {
                    "token_address": token_addr,
                    "symbol": data["symbol"],
                    "logo": token_info.get("logo"),
                    "invested": data["invested"],
                    "recovered": data["recovered"],
                    "pnl": pnl,
                    "roi_percent": roi,
                    "buys": data["buys"],
                    "sells": data["sells"],
                    "status": "profitable" if pnl > 0 else "losing" if pnl < 0 else "neutral"
                }

                # Añadir dirección y recomendación
                if data["invested"] > data["recovered"]:
                    # Más invertido que recuperado - posición abierta o pérdidas
                    if pnl > 0:
                        token_flow["direction"] = "partial_profit_taken"
                        token_flow["explanation"] = f"Tienes ganancia de {pnl:.4f} SOL pero aún queda valor invertido"
                        token_flow["recommendation"] = "Considerar tomar más ganancias o mantener posición"
                    else:
                        token_flow["direction"] = "position_open"
                        token_flow["explanation"] = f"Tienes {data['invested'] - data['recovered']:.4f} SOL actualmente invertido en {data['symbol']}"
                        token_flow["recommendation"] = "Monitorizar precio para decidir cuándo tomar ganancias o cortar pérdidas"

                elif data["recovered"] > data["invested"]:
                    # Más recuperado que invertido - ganancia total
                    token_flow["direction"] = "profit_taken"
                    token_flow["explanation"] = f"Has obtenido {pnl:.4f} SOL de ganancia total en {data['symbol']}"
                    token_flow["recommendation"] = "Excelente trade. Considerar repetir patrón"

                flows["token_flows"].append(token_flow)

        # Ordenar por ROI
        flows["token_flows"].sort(key=lambda x: x["roi_percent"], reverse=True)

        # Generar recomendaciones globales
        if flows["sol_flows"]["net"] < -10:
            flows["recommendations"].append({
                "type": "warning",
                "priority": "high",
                "title": "Alta Inversión Neta",
                "description": f"Tienes {abs(flows['sol_flows']['net']):.2f} SOL más invertido que recuperado. Considera tomar algunas ganancias.",
                "action": "Revisar posiciones abiertas y considerar sales parciales"
            })
        elif flows["sol_flows"]["net"] > 5:
            flows["recommendations"].append({
                "type": "success",
                "priority": "medium",
                "title": "Ganancias Netas Positivas",
                "description": f"Has generado {flows['sol_flows']['net']:.2f} SOL de ganancia total. ¡Buen trabajo!",
                "action": "Continuar con estrategia actual"
            })

        # Recomendación por mejor token
        profitable_tokens = [t for t in flows["token_flows"] if t["status"] == "profitable"]
        if profitable_tokens:
            best = profitable_tokens[0]
            flows["recommendations"].append({
                "type": "info",
                "priority": "low",
                "title": f"Mejor Token: {best['symbol']}",
                "description": f"{best['symbol']} te ha dado {best['pnl']:.4f} SOL ({best['roi_percent']:.1f}% ROI)",
                "action": "Analizar qué funcionó bien en este token"
            })

        return flows

    finally:
        await helius_api.close()


@router.get("/wallet/portfolio")
async def get_wallet_portfolio(wallet_address: str = Query(..., description="Solana wallet address")):
    """
    Obtiene el portfolio completo de la wallet
    Incluye balance SOL y todos los tokens con metadatos
    """
    if not SolanaRPCClient.is_valid_address(wallet_address):
        raise HTTPException(status_code=400, detail="Invalid wallet address")

    rpc_client = SolanaRPCClient()
    helius_api = HeliusAPI()

    try:
        # Obtener balance SOL
        sol_balance = await rpc_client.get_balance(wallet_address)

        # Obtener balances de tokens con metadatos
        tokens = await helius_api.get_wallet_token_balances(wallet_address)

        # Obtener precio de SOL para calcular valor USD
        try:
            sol_price_usd = await get_sol_price_usd()
        except Exception:
            sol_price_usd = 0

        # Calcular valor total
        total_value_usd = sol_balance * sol_price_usd if sol_price_usd > 0 else None

        return PortfolioResponse(
            wallet_address=wallet_address,
            sol_balance=sol_balance,
            total_tokens=len(tokens),
            tokens=tokens,
            total_value_usd=total_value_usd
        )

    except Exception as e:
        print(f"Error getting portfolio: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting portfolio: {str(e)}")

    finally:
        await rpc_client.close()
        await helius_api.close()


@router.post("/feedback/vote")
async def vote_feedback(
    signal_type: str = Query(..., description="Tipo de señal (ej. risk_reward, hold_time_bias)"),
    useful: bool = Query(..., description="¿Fue útil la recomendación?"),
    wallet_address: Optional[str] = Query(None, description="Wallet analizada"),
):
    """Registra el voto de un usuario sobre una recomendación.

    Con estos datos el sistema aprende qué señales son útiles para la
    comunidad y las prioriza en futuros análisis (mejora continua).
    """
    result = feedback_store.vote(signal_type, useful, wallet_address)
    return result


@router.get("/feedback/stats")
async def feedback_stats():
    """Estadísticas de feedback: qué señales son más útiles para la comunidad."""
    return feedback_store.stats()


@router.get("/community/benchmark")
async def community_benchmark(wallet_address: str = Query(..., description="Solana wallet address")):
    """Percentiles de la wallet frente a toda la comunidad analizada."""
    return community_store.benchmark(wallet_address)


@router.get("/community/evolution")
async def community_evolution(
    wallet_address: str = Query(..., description="Solana wallet address"),
    limit: int = Query(60, ge=1, le=200),
):
    """Historial de P&L/valor de la wallet persistido en el servidor."""
    return community_store.evolution(wallet_address, limit=limit)


@router.get("/community/stats")
async def community_stats():
    """Estadísticas agregadas de la comunidad (promedios y distribución)."""
    return community_store.community_stats()


@router.get("/system/ratelimit")
async def system_ratelimit():
    """Estado del rate limiter de Helius: hits, esperas y presión actual.

    El frontend lo consulta en cada ciclo de refresh para avisar cuando la
    API externa esté cerca del límite (útil con una comunidad de ~100 personas
    analizando a la vez).
    """
    from app.services.rate_limit import helius_limiter
    return helius_limiter.snapshot()
