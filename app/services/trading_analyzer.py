from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
import statistics
from app.models.schemas import (
    Transaction, TransactionType, TokenStats,
    TradingMetrics, PatternAnalysis, Recommendation, WalletProfile
)


class TradingAnalyzer:
    """Analiza transacciones y genera métricas de trading"""

    def __init__(self, transactions: List[Transaction]):
        self.transactions = sorted(transactions, key=lambda x: x.timestamp)
        self.token_trades = defaultdict(list)
        self._organize_by_token()

    def _organize_by_token(self):
        """Organiza transacciones por token"""
        for tx in self.transactions:
            if tx.type in [TransactionType.BUY, TransactionType.SELL, TransactionType.SWAP]:
                self.token_trades[tx.token_address].append(tx)

    def detect_profile(self) -> WalletProfile:
        """Detecta el estilo de trading de la wallet a partir de sus datos.

        Cada wallet es un mundo: la misma señal significa cosas distintas
        para un acumulador, un sniper o un scalper. Este perfil ajusta el
        lenguaje de las recomendaciones a cada caso.
        """
        metrics = self.calculate_metrics()
        closed_trades = self._get_closed_trades()
        buys = [t for t in self.transactions if t.type in (TransactionType.BUY, TransactionType.TRANSFER_IN)]
        sells = [t for t in self.transactions if t.type in (TransactionType.SELL, TransactionType.TRANSFER_OUT)]

        total = len(self.transactions)
        buy_count = len(buys)
        sell_count = len(sells)
        unique_tokens = len(self.token_trades)

        avg_hold = metrics.avg_hold_time_seconds or 0
        avg_size = metrics.avg_trade_size or 0
        sizes = [t.sol_amount for t in self.transactions if t.sol_amount and t.sol_amount > 0]
        max_size = max(sizes) if sizes else 0
        size_variance = (max_size / avg_size) if avg_size > 0 else 0

        # --- Clasificación por estilo ---
        profile = None

        # 1. Acumulador: compra mucho, casi no vende (estrategia de largo plazo)
        if total >= 3 and sell_count == 0:
            profile = ("accumulator", "Acumulador", "🐢",
                       "Compras constantes sin cerrar posiciones. Estás construyendo posición a largo plazo, "
                       "pero sin plan de salida tu capital queda expuesto al mercado.")
        # 2. Sniper: muchas compras pequeñas rápidas, ventas ocasionales
        elif total >= 10 and sell_count > 0 and (buy_count / max(sell_count, 1)) >= 2.5 and avg_hold <= 7200:
            profile = ("sniper", "Sniper", "🎯",
                       "Entras rápido y en tamaño pequeño en muchos tokens. Eres selectivo con las salidas, "
                       "pero las comisiones se acumulan con tanta actividad.")
        # 3. Scalper: volúmen alto, holds cortos (minutos), win rate bajo aceptado
        elif total >= 10 and avg_hold > 0 and avg_hold <= 1800 and (buy_count + sell_count) >= 10:
            profile = ("scalper", "Scalper", "⚡",
                       "Operas en ventanas de minutos. La velocidad es tu ventaja, pero cada trade "
                       "paga fees y slippage: vigila que el volumen no se coma tus márgenes.")
        # 4. Swing: holds de horas/días, tamaño medio (requiere volumen de trades)
        elif total >= 8 and 1800 < avg_hold <= 172800 and sell_count > 0:
            profile = ("swing", "Swing Trader", "🌊",
                       "Mantienes posiciones de horas a días buscando movimientos medios. "
                       "Tu reto es definir objetivos de ganancia claros y respetar los stops.")
        # 5. Hodler: pocos trades, holds muy largos
        elif total >= 2 and avg_hold > 172800:
            profile = ("hodler", "Hodler", "💎",
                       "Mantienes posiciones durante días o semanas. El tiempo en mercado es tu "
                       "estrategia; asegúrate de no dejar ganancias sin proteger.")
        # 6. Bot / Actividad mecánica: tamaño uniforme + mucha actividad + holds cortos
        elif total >= 15 and size_variance <= 2.5 and sell_count > 0 and avg_hold <= 3600:
            profile = ("bot", "Bot / Automatizado", "🤖",
                       "Tu patrón parece automatizado: tamaño de posición muy uniforme y actividad "
                       "constante. Revisa que la estrategia del bot tenga stop loss y gestión de riesgo.")

        # Perfil por defecto
        if profile is None:
            profile = ("balanced", "Equilibrado", "⚖️",
                       "Mezclas compras y ventas con tiempos variados. No hay un sesgo extremo; "
                       "el siguiente paso es afinar qué te funciona y duplicarlo.")

        style, label, emoji, description = profile

        # Fortalezas y debilidades según el estilo
        strengths = []
        weaknesses = []
        if sell_count > 0 and metrics.win_rate >= 50:
            strengths.append("Ganas más de la mitad de tus trades cerrados")
        if metrics.profit_factor >= 1.5:
            strengths.append(f"Profit factor sano ({metrics.profit_factor:.2f})")
        if style == "accumulator":
            strengths.append("Disciplina para acumular sin vender por pánico")
            weaknesses.append("Sin estrategia de salida: P&L depende del mercado")
        if style in ("sniper", "scalper"):
            strengths.append("Ejecución rápida en entradas")
            weaknesses.append("Las fees se acumulan con la frecuencia")
        if style in ("swing", "hodler"):
            strengths.append("Paciencia para dejar correr las posiciones")
            weaknesses.append("Riesgo de no tomar ganancias a tiempo")
        if sell_count == 0 and total >= 5:
            weaknesses.append("Cero ventas: todo el capital invertido está expuesto")
        if metrics.win_rate > 0 and metrics.win_rate < 35 and sell_count > 0:
            weaknesses.append(f"Win rate bajo ({metrics.win_rate:.0f}%) en trades cerrados")
        if not strengths:
            strengths.append("Actividad registrada y analizada")
        if not weaknesses:
            weaknesses.append("Sin señales críticas detectadas")

        return WalletProfile(
            style=style,
            label=label,
            emoji=emoji,
            description=description,
            strengths=strengths[:3],
            weaknesses=weaknesses[:3],
            signals={
                "total_transactions": total,
                "buy_count": buy_count,
                "sell_count": sell_count,
                "unique_tokens": unique_tokens,
                "avg_hold_seconds": round(avg_hold, 1),
                "size_variance": round(size_variance, 2),
            }
        )

    def analyze_all_tokens(self) -> List[TokenStats]:
        """Analiza todos los tokens tradeados"""
        stats = []

        for token_address, trades in self.token_trades.items():
            token_stats = self.analyze_token(token_address, trades)
            if token_stats:
                stats.append(token_stats)

        # Ordenar por PnL total
        stats.sort(key=lambda x: x.total_pnl, reverse=True)
        return stats

    def analyze_token(self, token_address: str, trades: List[Transaction]) -> Optional[TokenStats]:
        """Analiza un token específico"""
        if not trades:
            return None

        buys = [t for t in trades if t.type in [TransactionType.BUY, TransactionType.TRANSFER_IN]]
        sells = [t for t in trades if t.type in [TransactionType.SELL, TransactionType.TRANSFER_OUT]]

        total_bought = sum(t.token_amount for t in buys)
        total_sold = sum(t.token_amount for t in sells)
        total_spent = sum(t.sol_amount for t in buys)
        total_received = sum(t.sol_amount for t in sells)

        # PnL realizado: prorratear el coste según lo vendido (FIFO de coste medio).
        # Si no se ha vendido nada, realized_pnl es 0 (aún se mantiene, no es pérdida).
        sold_ratio = (total_sold / total_bought) if total_bought > 0 else 0
        cost_of_sold = total_spent * sold_ratio
        realized_pnl = total_received - cost_of_sold

        # PnL no realizado (tokens restantes)
        current_holdings = total_bought - total_sold
        avg_buy_price = total_spent / total_bought if total_bought > 0 else 0
        unrealized_pnl = 0  # Requeriría precio actual

        total_pnl = realized_pnl + unrealized_pnl

        # ROI
        roi_percent = (total_pnl / cost_of_sold * 100) if cost_of_sold > 0 else 0

        # Timestamps
        first_buy = min((t.timestamp for t in buys), default=None)
        last_sell = max((t.timestamp for t in sells), default=None)

        # Precios promedio
        avg_buy_price = sum(t.price_per_token for t in buys) / len(buys) if buys else 0
        avg_sell_price = sum(t.price_per_token for t in sells) / len(sells) if sells else 0

        return TokenStats(
            token_address=token_address,
            token_symbol=trades[0].token_symbol,
            token_logo=trades[0].token_logo,
            total_bought=total_bought,
            total_sold=total_sold,
            total_volume_sol=total_spent + total_received,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            total_pnl=total_pnl,
            roi_percent=roi_percent,
            trades_count=len(trades),
            first_buy=first_buy,
            last_sell=last_sell,
            avg_buy_price=avg_buy_price,
            avg_sell_price=avg_sell_price,
            current_holdings=current_holdings,
            is_still_holding=current_holdings > 0.000001
        )

    def calculate_metrics(self) -> TradingMetrics:
        """Calcula métricas generales de trading"""
        closed_trades = self._get_closed_trades()

        if not closed_trades:
            # Sin trades cerrados (ej. wallet que solo compra): las métricas
            # de actividad sí existen y no deben aparecer como cero.
            volumes = [t.sol_amount for t in self.transactions if t.sol_amount and t.sol_amount > 0]
            total_volume = sum(volumes)
            avg_size = total_volume / len(volumes) if volumes else 0
            return TradingMetrics(
                total_trades=len(self.transactions),
                winning_trades=0,
                losing_trades=0,
                win_rate=0,
                total_pnl=0,
                total_volume=round(total_volume, 6),
                total_fees=round(sum(t.fee for t in self.transactions), 6),
                avg_trade_size=round(avg_size, 6),
                avg_hold_time_seconds=0,
                largest_win=0,
                largest_loss=0,
                profit_factor=0,
                sharpe_ratio=None
            )

        pnls = [t["pnl"] for t in closed_trades]
        winning = [p for p in pnls if p > 0]
        losing = [p for p in pnls if p < 0]

        total_trades = len(closed_trades)
        winning_trades = len(winning)
        losing_trades = len(losing)
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

        total_pnl = sum(pnls)
        total_volume = sum(t["volume"] for t in closed_trades)
        total_fees = sum(t["fee"] for t in closed_trades)
        avg_trade_size = total_volume / total_trades if total_trades > 0 else 0

        hold_times = [t["hold_time"] for t in closed_trades if t["hold_time"]]
        avg_hold_time = sum(hold_times) / len(hold_times) if hold_times else 0

        largest_win = max(winning) if winning else 0
        largest_loss = min(losing) if losing else 0

        # Profit Factor: (gross profit) / (gross loss)
        gross_profit = sum(winning) if winning else 0
        gross_loss = abs(sum(losing)) if losing else 0.000001
        profit_factor = gross_profit / gross_loss

        # Sharpe Ratio (simplificado)
        sharpe_ratio = None
        if len(pnls) > 1:
            try:
                avg_return = statistics.mean(pnls)
                std_dev = statistics.stdev(pnls)
                sharpe_ratio = (avg_return / std_dev) if std_dev > 0 else 0
            except:
                pass

        return TradingMetrics(
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=round(win_rate, 2),
            total_pnl=round(total_pnl, 6),
            total_volume=round(total_volume, 6),
            total_fees=round(total_fees, 6),
            avg_trade_size=round(avg_trade_size, 6),
            avg_hold_time_seconds=round(avg_hold_time, 2),
            largest_win=round(largest_win, 6),
            largest_loss=round(largest_loss, 6),
            profit_factor=round(profit_factor, 2),
            sharpe_ratio=round(sharpe_ratio, 2) if sharpe_ratio else None
        )

    def _get_closed_trades(self) -> List[Dict]:
        """Identifica trades cerrados (buy + sell del mismo token)"""
        closed = []

        for token_address, trades in self.token_trades.items():
            buys = [t for t in trades if t.type in [TransactionType.BUY, TransactionType.TRANSFER_IN]]
            sells = [t for t in trades if t.type in [TransactionType.SELL, TransactionType.TRANSFER_OUT]]

            # Emparejar buys con sells (FIFO)
            buy_queue = []
            for buy in sorted(buys, key=lambda x: x.timestamp):
                buy_queue.append(buy)

            for sell in sorted(sells, key=lambda x: x.timestamp):
                if not buy_queue:
                    continue

                buy = buy_queue.pop(0)

                # Calcular PnL de este trade
                pnl = (sell.price_per_token - buy.price_per_token) * sell.token_amount
                volume = buy.sol_amount
                fee = buy.fee + sell.fee
                hold_time = (sell.timestamp - buy.timestamp).total_seconds()

                closed.append({
                    "token": token_address,
                    "pnl": pnl,
                    "volume": volume,
                    "fee": fee,
                    "hold_time": hold_time
                })

        return closed

    def analyze_patterns(self) -> List[PatternAnalysis]:
        """Analiza patrones de trading"""
        patterns = []

        # 1. Análisis de hold time
        closed_trades = self._get_closed_trades()
        if closed_trades:
            hold_times = [t["hold_time"] for t in closed_trades if t["hold_time"] > 0]
            if hold_times:
                avg_hold = sum(hold_times) / len(hold_times)

                if avg_hold < 3600:  # Menos de 1 hora
                    patterns.append(PatternAnalysis(
                        description="Trading de muy corto plazo",
                        confidence=0.8,
                        suggestion="Considera mantener posiciones más tiempo para reducir el impacto de fees"
                    ))
                elif avg_hold > 86400 * 7:  # Más de 7 días
                    patterns.append(PatternAnalysis(
                        description="Trading de largo plazo",
                        confidence=0.7,
                        suggestion="Monitorea más activamente tus posiciones para tomar profit"
                    ))

        # 2. Análisis de tamaño de trades
        if len(self.transactions) > 5:
            volumes = [t.sol_amount for t in self.transactions if t.sol_amount > 0]
            if volumes:
                avg_size = sum(volumes) / len(volumes)
                max_size = max(volumes)

                if max_size > avg_size * 5:
                    patterns.append(PatternAnalysis(
                        description="Alta variación en tamaño de trades",
                        confidence=0.75,
                        suggestion="Considera usar un tamaño de posición más consistente"
                    ))

        # 3. Análisis de winners vs losers
        metrics = self.calculate_metrics()
        if metrics.total_trades > 5:
            if metrics.win_rate < 30:
                patterns.append(PatternAnalysis(
                    description="Bajo win rate",
                    confidence=0.9,
                    suggestion="Revisa tu estrategia de entrada. Quizás estás entrando demasiado tarde."
                ))
            elif metrics.win_rate > 70:
                patterns.append(PatternAnalysis(
                    description="Alto win rate",
                    confidence=0.85,
                    suggestion="Buen trabajo. Considera aumentar el tamaño de tus ganadores."
                ))

        # 4. Análisis de profit factor
        if metrics.total_trades == 0:
            pass  # Sin trades cerrados no hay señal de profit factor
        elif metrics.profit_factor < 1:
            patterns.append(PatternAnalysis(
                description="Perdiendo dinero netamente",
                confidence=0.95,
                suggestion="Tus pérdidas superan a tus ganancias. Reduce tamaño de posiciones o mejora tus stop losses."
            ))
        elif metrics.profit_factor > 2:
            patterns.append(PatternAnalysis(
                description="Excelente profit factor",
                confidence=0.9,
                suggestion="Estás gestionando muy bien el riesgo. Mantén esta estrategia."
            ))

        return patterns

    def calculate_trading_score(self) -> Optional["TradingScore"]:
        """Puntuación global de trading 0-100 con desglose accionable.

        Cuatro dimensiones ponderadas:
        - Rentabilidad (30%): P&L total + profit factor
        - Consistencia (25%): win rate + Sharpe
        - Gestión de riesgo (30%): relación riesgo/recompensa y disciplina de stops
        - Eficiencia (15%): coste de fees sobre volumen

        El resultado es una nota única y comprensible ("Sólido", "En riesgo"...)
        con el desglose para que el usuario vea qué dimensión le penaliza.
        """
        from app.models.schemas import TradingScore

        metrics = self.calculate_metrics()
        closed_trades = self._get_closed_trades()
        if not closed_trades:
            return None

        # --- Rentabilidad (0-100) ---
        rent = 0.0
        # Profit factor: 0..2+ mapeado a 0..60
        pf_score = min(60, max(0, metrics.profit_factor) / 2.0 * 60)
        # P&L: negativo penaliza, positivo recompensa hasta +40
        if metrics.total_pnl > 0:
            pnl_score = 40
        elif metrics.total_pnl < 0:
            pnl_score = max(0, 40 - abs(metrics.total_pnl) * 20)
        else:
            pnl_score = 20
        rent = pf_score + pnl_score

        # --- Consistencia (0-100) ---
        cons = 0.0
        cons += (metrics.win_rate / 100.0) * 60  # win rate hasta 60
        if metrics.sharpe_ratio is not None:
            cons += min(40, max(0, metrics.sharpe_ratio) / 1.0 * 40)  # sharpe hasta 40
        else:
            cons += 15  # sin datos suficientes

        # --- Gestión de riesgo (0-100) ---
        riesgo = 50.0  # base neutra
        winning = [t["pnl"] for t in closed_trades if t["pnl"] > 0]
        losing = [t["pnl"] for t in closed_trades if t["pnl"] < 0]
        avg_win = self._avg(winning)
        avg_loss = abs(self._avg(losing))
        if avg_win > 0 and avg_loss > 0:
            rr = avg_win / avg_loss
            riesgo = min(100, max(0, rr / 2.0 * 100))  # rr=2 → 100
        # Pérdidas desproporcionadas respecto al tamaño medio penalizan
        if metrics.avg_trade_size > 0 and avg_loss > metrics.avg_trade_size * 0.3:
            riesgo -= 15
        # Si no hay pérdidas (solo compras), el riesgo es desconocido: neutro bajo
        if not losing and metrics.win_rate == 0:
            riesgo = 35

        # --- Eficiencia (0-100) ---
        efic = 100.0
        if metrics.total_volume > 0:
            fee_pct = metrics.total_fees / metrics.total_volume * 100
            efic = max(0, 100 - fee_pct * 30)  # 3.3% de fees → 0

        # --- Ponderación final ---
        total = (
            rent * 0.30
            + cons * 0.25
            + riesgo * 0.30
            + efic * 0.15
        )
        total = round(min(100, max(0, total)), 1)

        if total >= 75:
            grade, label = "A", "Sólido"
        elif total >= 60:
            grade, label = "B", "Competente"
        elif total >= 45:
            grade, label = "C", "En desarrollo"
        elif total >= 30:
            grade, label = "D", "En riesgo"
        else:
            grade, label = "E", "Crítico"

        weak = min(
            [("rentabilidad", rent), ("consistencia", cons), ("riesgo", riesgo), ("eficiencia", efic)],
            key=lambda x: x[1],
        )
        summary = (
            f"Nota {label} ({grade}). Tu punto más débil es {weak[0]} ({weak[1]:.0f}/100): "
            + {
                "rentabilidad": "necesitas más ganancias netas y un mejor profit factor.",
                "consistencia": "tu tasa de acierto o Sharpe te penalizan; busca setups más repetibles.",
                "riesgo": "tus pérdidas son grandes frente a tus ganancias; aprieta los stops.",
                "eficiencia": "las fees consumen demasiado margen; opera menos y con mejor selección.",
            }[weak[0]]
        )

        return TradingScore(
            total=total,
            rentabilidad=round(rent, 1),
            consistencia=round(cons, 1),
            riesgo=round(riesgo, 1),
            eficiencia=round(efic, 1),
            label=label,
            grade=grade,
            summary=summary,
        )

    def analyze_time_of_day(self) -> Optional["TimeOfDayInsight"]:
        """Analiza en qué franja horaria la wallet gana y pierde dinero.

        Agrupa los trades cerrados por hora del día (hora local del
        timestamp de la transacción) y calcula el P&L por hora. Esto
        permite decirle al usuario cuándo opera mejor, en lugar de
        consejos genéricos.
        """
        from app.models.schemas import TimeOfDayInsight

        closed_trades = self._get_closed_trades()
        if not closed_trades:
            return None

        pnl_by_hour = defaultdict(float)
        trades_by_hour = defaultdict(int)
        win_by_hour = defaultdict(int)

        # Necesitamos el timestamp del trade cerrado: lo reconstruimos
        # emparejando igual que _get_closed_trades (buy + sell FIFO).
        hour_by_trade = []
        for token_address, trades in self.token_trades.items():
            buys = [t for t in trades if t.type in [TransactionType.BUY, TransactionType.TRANSFER_IN]]
            sells = [t for t in trades if t.type in [TransactionType.SELL, TransactionType.TRANSFER_OUT]]
            buy_queue = list(sorted(buys, key=lambda x: x.timestamp))
            for sell in sorted(sells, key=lambda x: x.timestamp):
                if not buy_queue:
                    continue
                buy = buy_queue.pop(0)
                pnl = (sell.price_per_token - buy.price_per_token) * sell.token_amount
                # Hora de la venta (cuando se materializa el resultado)
                hour = sell.timestamp.hour
                pnl_by_hour[hour] += pnl
                trades_by_hour[hour] += 1
                if pnl > 0:
                    win_by_hour[hour] += 1

        if not trades_by_hour:
            return None

        hours = sorted(trades_by_hour.keys())
        best_hour = max(hours, key=lambda h: pnl_by_hour[h])
        worst_hour = min(hours, key=lambda h: pnl_by_hour[h])

        by_hour = [
            {
                "hour": h,
                "pnl": round(pnl_by_hour[h], 6),
                "trades": trades_by_hour[h],
                "wins": win_by_hour[h],
            }
            for h in hours
        ]

        # Resumen legible
        def fmt_hour(h: int) -> str:
            return f"{h:02d}:00"

        best_pct = (win_by_hour[best_hour] / trades_by_hour[best_hour] * 100) if trades_by_hour[best_hour] else 0
        summary = (
            f"Tu mejor franja es {fmt_hour(best_hour)} ({pnl_by_hour[best_hour]:+.4f} SOL en "
            f"{trades_by_hour[best_hour]} trades, {best_pct:.0f}% de acierto). "
            f"La peor es {fmt_hour(worst_hour)} ({pnl_by_hour[worst_hour]:+.4f} SOL). "
            f"Concentra tu atención en las horas donde históricamente ganas."
        )

        return TimeOfDayInsight(
            best_hour=best_hour,
            best_hour_pnl=round(pnl_by_hour[best_hour], 6),
            best_hour_trades=trades_by_hour[best_hour],
            worst_hour=worst_hour,
            worst_hour_pnl=round(pnl_by_hour[worst_hour], 6),
            by_hour=by_hour,
            summary=summary,
        )

    def _avg(self, values: List[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    def generate_recommendations(self) -> List[Recommendation]:
        """Genera recomendaciones personalizadas y accionables por wallet.

        Combina métricas de riesgo (riesgo/recompensa, stop loss, fees),
        comportamiento (hold time, revenge trading, consistencia) y
        oportunidades (mejores tokens, diversificación).
        """
        recommendations = []
        metrics = self.calculate_metrics()
        token_stats = self.analyze_all_tokens()
        closed_trades = self._get_closed_trades()

        winning_trades = [t for t in closed_trades if t["pnl"] > 0]
        losing_trades = [t for t in closed_trades if t["pnl"] < 0]
        avg_win = self._avg([t["pnl"] for t in winning_trades])
        avg_loss = abs(self._avg([t["pnl"] for t in losing_trades]))

        # --- 1. Riesgo / Recompensa (la señal más informativa) ---
        if avg_win > 0 and avg_loss > 0:
            rr_ratio = avg_win / avg_loss if avg_loss else 0
            if rr_ratio < 0.8:
                recommendations.append(Recommendation(
                    type="risk_reward",
                    priority="high",
                    title="Tu relación riesgo/recompensa es desfavorable",
                    description=(
                        f"Ganas en promedio {avg_win:.4f} SOL por trade ganador, pero pierdes "
                        f"{avg_loss:.4f} SOL por trade perdedor (ratio {rr_ratio:.2f}). "
                        f"Necesitas ganar {avg_loss / avg_win:.1f}x más veces de las que pierdes para ser rentable. "
                        f"Objetivo: buscar trades con al menos 1.5x de recompensa sobre riesgo."
                    ),
                    actionable=True,
                    data={"risk_reward_ratio": round(rr_ratio, 2), "avg_win": round(avg_win, 6), "avg_loss": round(avg_loss, 6)}
                ))

        # --- 2. Sesgo de hold time: cortar ganadores / aguantar perdedores ---
        win_holds = [t["hold_time"] for t in winning_trades if t["hold_time"] > 0]
        loss_holds = [t["hold_time"] for t in losing_trades if t["hold_time"] > 0]
        avg_win_hold = self._avg(win_holds)
        avg_loss_hold = self._avg(loss_holds)
        if avg_win_hold > 0 and avg_loss_hold > 0 and avg_loss_hold > avg_win_hold * 1.5:
            recommendations.append(Recommendation(
                type="hold_time_bias",
                priority="high",
                title="Estás aguantando perdedores y cortando ganadores",
                description=(
                    f"Mantienes tus trades perdedores ~{avg_loss_hold/3600:.1f}h de media, "
                    f"pero solo ~{avg_win_hold/3600:.1f}h tus ganadores. Es el patrón inverso al rentable: "
                    f"deja correr ganancias y corta pérdidas rápido (stop loss temprano)."
                ),
                actionable=True,
                data={"avg_win_hold_seconds": round(avg_win_hold, 2), "avg_loss_hold_seconds": round(avg_loss_hold, 2)}
            ))

        # --- 3. Asimetría: muchos trades pequeños ganadores, pocas pérdidas grandes ---
        if metrics.win_rate >= 60 and metrics.total_pnl < 0 and avg_loss > 0:
            recommendations.append(Recommendation(
                type="asymmetry",
                priority="high",
                title="Ganas a menudo pero pierdes más de lo que ganas",
                description=(
                    f"Tu win rate es del {metrics.win_rate:.0f}% pero el P&L total es negativo. "
                    f"Las pérdidas grandes ({avg_loss:.4f} SOL de media) borran muchas ganancias pequeñas "
                    f"({avg_win:.4f} SOL). Aplica un stop loss duro para limitar el tamaño de cada pérdida."
                ),
                actionable=True,
                data={"win_rate": metrics.win_rate, "avg_win": round(avg_win, 6), "avg_loss": round(avg_loss, 6)}
            ))

        # --- 4. Stop Loss ---
        if losing_trades:
            if avg_loss > metrics.avg_trade_size * 0.3 and metrics.avg_trade_size > 0:
                loss_pct = abs(avg_loss / metrics.avg_trade_size * 100)
                recommendations.append(Recommendation(
                    type="risk_management",
                    priority="high",
                    title="Implementa stop loss más estrictos",
                    description=(
                        f"Tus pérdidas promedio son del {loss_pct:.1f}% del tamaño del trade. "
                        f"Define un stop loss fijo (-15% a -20%) antes de entrar para que nunca "
                        f"pierdas más de lo planeado."
                    ),
                    actionable=True,
                    data={"avg_loss_percent": round(loss_pct, 1)}
                ))

        # --- 5. Tamaño de posición: consistencia ---
        volumes = [t.sol_amount for t in self.transactions if t.sol_amount > 0]
        if len(volumes) >= 5 and metrics.avg_trade_size > 0:
            max_vol = max(volumes)
            if max_vol > metrics.avg_trade_size * 3:
                recommendations.append(Recommendation(
                    type="position_sizing",
                    priority="medium",
                    title="Tu tamaño de posición es demasiado variable",
                    description=(
                        f"Tu trade más grande ({max_vol:.4f} SOL) es {max_vol/metrics.avg_trade_size:.1f}x tu promedio. "
                        f"Posiciones de tamaño consistente protegen tu capital y hacen el resultado predecible. "
                        f"Usa máximo un 1-3% de tu cartera por trade."
                    ),
                    actionable=True,
                    data={"max_size": round(max_vol, 6), "avg_size": round(metrics.avg_trade_size, 6)}
                ))

        # --- 6. Revenge trading: compra grande justo después de una pérdida ---
        if len(self.transactions) >= 4:
            for i in range(1, len(self.transactions)):
                prev, curr = self.transactions[i - 1], self.transactions[i]
                time_gap = (curr.timestamp - prev.timestamp).total_seconds()
                if (
                    prev.type == TransactionType.SELL
                    and curr.type == TransactionType.BUY
                    and time_gap < 600  # menos de 10 minutos
                    and curr.sol_amount > prev.sol_amount * 1.5  # y el doble de tamaño
                ):
                    recommendations.append(Recommendation(
                        type="revenge_trading",
                        priority="medium",
                        title="Detectamos posible 'revenge trading'",
                        description=(
                            f"Después de vender {prev.token_symbol or 'un token'} por {prev.sol_amount:.4f} SOL, "
                            f"reentraste {curr.sol_amount:.4f} SOL en menos de 10 minutos. "
                            f"Tradear emocionalmente tras una pérdida suele amplificar el daño. Espera y planea."
                        ),
                        actionable=True,
                        data={"gap_seconds": round(time_gap, 2)}
                    ))
                    break

        # --- 7. Impacto de fees ---
        if metrics.total_volume > 0:
            fee_pct = metrics.total_fees / metrics.total_volume * 100
            if fee_pct > 0.5:
                recommendations.append(Recommendation(
                    type="cost_reduction",
                    priority="medium",
                    title="Las fees están comiendo tus márgenes",
                    description=(
                        f"Has pagado {metrics.total_fees:.4f} SOL en fees ({fee_pct:.2f}% del volumen). "
                        f"Consolida trades, evita operar con slippage alto y usa rutas con menor costo."
                    ),
                    actionable=True,
                    data={"total_fees": round(metrics.total_fees, 6), "fee_percent": round(fee_pct, 2)}
                ))

        # --- 7b. Sin estrategia de salida: solo compras, ninguna venta ---
        buy_count = sum(1 for t in self.transactions if t.type in (TransactionType.BUY, TransactionType.TRANSFER_IN))
        sell_count = sum(1 for t in self.transactions if t.type in (TransactionType.SELL, TransactionType.TRANSFER_OUT))
        if buy_count >= 5 and sell_count == 0:
            total_invested = sum(t.sol_amount for t in self.transactions if t.sol_amount and t.sol_amount > 0)
            recommendations.append(Recommendation(
                type="no_exit_strategy",
                priority="high",
                title="Solo compras: no has vendido nada todavía",
                description=(
                    f"Has invertido ~{total_invested:.4f} SOL en {buy_count} compras sin cerrar ninguna posición. "
                    f"El P&L no realizado depende del precio actual. Define un objetivo de ganancia y un "
                    f"stop loss por token antes de seguir acumulando, y evita promediar a la baja sin plan."
                ),
                actionable=True,
                data={"total_invested_sol": round(total_invested, 6), "buy_count": buy_count}
            ))

        # --- 8. Oportunidad: tokens donde eres rentable ---
        winners = [t for t in token_stats if t.total_pnl > 0]
        if winners:
            best_token = max(winners, key=lambda t: t.total_pnl)
            recommendations.append(Recommendation(
                type="opportunity",
                priority="low",
                title=f"Tu edge está en {best_token.token_symbol or 'este token'}",
                description=(
                    f"Generaste {best_token.total_pnl:.4f} SOL ({best_token.roi_percent:.1f}% ROI) con "
                    f"{best_token.trades_count} trades de {best_token.token_symbol or 'este token'}. "
                    f"Estudia qué distingue a tus operaciones ganadoras y repítelo."
                ),
                actionable=False,
                data={"token_address": best_token.token_address, "pnl": round(best_token.total_pnl, 6), "roi": round(best_token.roi_percent, 2)}
            ))

        # --- 9. Diversificación ---
        if len(token_stats) < 3 and metrics.total_trades > 10:
            recommendations.append(Recommendation(
                type="diversification",
                priority="medium",
                title="Estás muy concentrado en pocos tokens",
                description=(
                    f"Solo has operado {len(token_stats)} tokens distintos en {metrics.total_trades} trades. "
                    f"La concentración amplifica tanto ganancias como pérdidas; diversifica para suavizar la curva."
                ),
                actionable=True,
                data={"unique_tokens": len(token_stats)}
            ))

        # --- 10. Señal positiva ---
        if metrics.win_rate >= 60 and metrics.profit_factor >= 2 and metrics.total_pnl > 0:
            recommendations.append(Recommendation(
                type="positive",
                priority="low",
                title="Tu sistema está funcionando: protégelo",
                description=(
                    f"Win rate del {metrics.win_rate:.0f}%, profit factor de {metrics.profit_factor:.2f} "
                    f"y {metrics.total_pnl:.4f} SOL de beneficio. No cambies la estrategia: solo "
                    f"refina el tamaño de posición y respeta tus stops."
                ),
                actionable=False,
                data={"win_rate": metrics.win_rate, "profit_factor": metrics.profit_factor, "total_pnl": round(metrics.total_pnl, 6)}
            ))

        # --- 10b. Ventana horaria óptima: cuándo conviene operar ---
        tod = self.analyze_time_of_day()
        if tod and tod.best_hour is not None and tod.best_hour_trades >= 1:
            recommendations.append(Recommendation(
                type="best_hours",
                priority="medium",
                title=f"Tu ventana óptima es {tod.best_hour:02d}:00",
                description=(
                    f"Históricamente ganas {tod.best_hour_pnl:+.4f} SOL en la franja de las "
                    f"{tod.best_hour:02d}:00 (hora UTC) con {tod.best_hour_trades} trades cerrados. "
                    f"Si usas auto-trading, programa tus entradas en esa franja y evita "
                    f"las {tod.worst_hour:02d}:00, tu peor momento ({tod.worst_hour_pnl:+.4f} SOL)."
                ),
                actionable=True,
                data={"best_hour": tod.best_hour, "worst_hour": tod.worst_hour,
                      "best_pnl": round(tod.best_hour_pnl, 6), "worst_pnl": round(tod.worst_hour_pnl, 6)}
            ))

        # --- 11. Señal de perfil: la recomendación más importante de cada estilo ---
        profile = self.detect_profile()
        profile_advice = {
            "accumulator": (
                "Define ya tu plan de salida",
                f"Acumulas sin vender (perfil {profile.label.lower()}). Asigna a cada token un objetivo de "
                f"ganancia y un stop loss ANTES de la siguiente compra; así el mercado no decide por ti."
            ),
            "sniper": (
                "Reduce frecuencia, sube calidad",
                f"Tu perfil es {profile.label.lower()}: muchas entradas pequeñas. Antes de cada compra "
                f"pregunta si el setup es 3x mejor que el anterior; la calidad sobre la cantidad."
            ),
            "scalper": (
                "Vigila fees y slippage",
                f"Como {profile.label.lower()}, cada trade paga comisión. Calcula si el movimiento esperado "
                f"supera al menos 3x las fees antes de entrar; de lo contrario el broker gana siempre."
            ),
            "swing": (
                "Define objetivos y stops",
                f"Tu perfil {profile.label.lower()} aguanta horas o días: fija un take-profit y un stop "
                f"por posición al entrar para no decidir con la emoción cuando el precio se mueve."
            ),
            "hodler": (
                "Protege tus ganancias",
                f"Como {profile.label.lower()}, el tiempo es tu aliado. Considera vender parcialmente en "
                f"picos y usar stops de protección para no devolver ganancias acumuladas."
            ),
            "bot": (
                "Audita la estrategia del bot",
                f"Tu patrón ({profile.label.lower()}) parece automatizado. Revisa que tenga stop loss, "
                f"límite de tamaño y pausa ante drawdown; un bot sin riesgo quema la cuenta."
            ),
            "balanced": (
                "Duplica lo que funciona",
                f"Perfil {profile.label.lower()}: identifica tus 2-3 trades más rentables del mes y "
                f"analiza qué tuvieron en común (token, timing, tamaño). Repite el patrón, no el azar."
            ),
        }
        advice_title, advice_desc = profile_advice.get(
            profile.style, ("Sigue tu plan", "Revisa tu estrategia y mantén disciplina.")
        )
        recommendations.append(Recommendation(
            type=f"profile_{profile.style}",
            priority="medium",
            title=advice_title,
            description=advice_desc,
            actionable=True,
            data={"profile": profile.style, "label": profile.label, "emoji": profile.emoji}
        ))

        # Ordenar combinando prioridad base con lo aprendido del feedback
        # de la comunidad: las señales que la comunidad marca como útiles
        # suben de posición (mejora continua por wallet/comunidad).
        priority_order = {"high": 0, "medium": 1, "low": 2}
        try:
            from app.services.feedback import feedback_store
            learned = feedback_store.signal_scores()
        except Exception:
            learned = {}

        def sort_key(rec: Recommendation):
            base = priority_order.get(rec.priority, 3)
            # Señales con score alto (>0.7) suben; las poco útiles (<0.3) bajan
            score = learned.get(rec.type, 0.5)
            boost = -0.2 if score >= 0.7 else (0.2 if score <= 0.3 else 0.0)
            return base + boost

        recommendations.sort(key=sort_key)
        return recommendations
