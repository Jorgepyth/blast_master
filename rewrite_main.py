import re

with open("cli/main.py", "r") as f:
    content = f.read()

# The block to replace is inside `elif audit_choice == "tac":`, `if t_status == TradeStatus.NO_TAKEN: ... else: while True:`
# Let's find the start of `else:` and `while True:` around line 2524
search_str = """                else:
                    while True:
                        htf_trend = session.prompt("htf_trend", get_enum_choice, "HTF Trend Context", HTFTrendContext)
                        ltf_trend = session.prompt("ltf_trend", get_enum_choice, "LTF Trend Context", TrendContext)
                        confirmation_5m_15m = session.prompt("confirmation_5m_15m", lambda: bind_pause(inquirer.select(
                            message="5m_15m_confirmation >",
                            choices=[Choice("yes", name="yes"), Choice("no", name="no")],
                            pointer=">",
                            qmark=""
                        )).execute())
                        sl = session.prompt("sl", get_mandatory_float, "Stop Loss")
                        entry_p = session.prompt("entry_p", get_mandatory_float, "Entry Price")
                        conf_params = session.prompt("conf_params", get_multi_enum_choice, "Confirmation Params", ConfirmationParams)
                        size = session.prompt("size", get_mandatory_float, "Size")
                        tp = session.prompt("tp", get_mandatory_float, "Take Profit")
                        entry_time = session.prompt("entry_time", get_mandatory_datetime, "Entry Time")
                        emotions = session.prompt("emotions", get_multi_enum_choice, "Emotions", Emotions)
                        pre_trade_emotions = session.prompt("pre_trade_emotions", get_mandatory_text, "Pre Trade Emotions")
                        p_emotion = session.prompt("p_emotion", get_enum_choice, "Primary Emotion", PrimaryEmotion)
                        mental_clarity = session.prompt("mental_clarity", get_mandatory_int, "Mental Clarity Level", 1, 5)
                        impatience = session.prompt("impatience", get_mandatory_int, "Impatience Level", 1, 5)
                        anxiety = session.prompt("anxiety", get_mandatory_int, "Anxiety Level", 1, 5)
                        mid_trade_emotions = session.prompt("mid_trade_emotions", get_mandatory_text, "Mid Trade Emotions")
                        post_trade_emotions = session.prompt("post_trade_emotions", get_mandatory_text, "Post Trade Emotions")
                        exit_time = session.prompt("exit_time", get_mandatory_datetime, "Exit Time")
                        exit_type = session.prompt("exit_type", get_enum_choice, "Exit Type", ExitType)
                        conf_status = session.prompt("conf_status", get_enum_choice, "Confirmation Status", ConfirmationStatus)
                        close_p = session.prompt("close_p", get_mandatory_float, "Closing Price")
                        
                        def ask_could_hit_tp():
                            return bind_pause(inquirer.select(
                                message="Could hit TP? >",
                                choices=[Choice("yes", name="yes"), Choice("no", name="no")],
                                pointer=">",
                                qmark="",
                                keybindings={"skip": []}
                            )).execute()
                            
                        could_hit_tp = session.prompt("could_hit_tp", ask_could_hit_tp)
                        t_comp = session.prompt("t_comp", get_enum_choice, "Compliance State", ComplianceState)
                        tier_setup = session.prompt("tier_setup", get_enum_choice, "Tier Setup", TierSetup)
                        market_state = session.prompt("market_state", get_enum_choice, "Market State", MarketState)
                        f_plan = session.prompt("f_plan", get_enum_choice, "Followed Plan", FollowedPlan)
                        setup_t = session.prompt("setup_t", get_enum_choice, "Setup Type", SetupType)
                        behav_errors = session.prompt("behav_errors", get_multi_enum_choice, "Behavioral Errors", BehavioralErrors)
                        cog_patterns = session.prompt("cog_patterns", get_multi_enum_choice, "Cognitive Patterns", CognitivePatterns)
                        mae = session.prompt("mae", get_mandatory_float, "MAE (0 <= MAE <= 10)", min_val=0, max_val=10)
                        mfe = session.prompt("mfe", get_mandatory_float, "MFE (0 <= MFE <= 10)", min_val=0, max_val=10)
                        cost = session.prompt("cost", get_mandatory_float, "Cost (Fees/Funding)")
                        lesson_tact = session.prompt("lesson_tact", get_mandatory_text, "Tactical Lesson Learned", multiline=True)
                        visual_path = session.prompt("visual_lesson_path", handle_visual_lesson_assignment, trade_id, payload.get("asset", "Unknown"))

                        audit_tactical = TacticalAudit("""


replacement_str = """                else:
                    while True:
                        sl = session.prompt("sl", get_mandatory_float, "Stop Loss")
                        entry_p = session.prompt("entry_p", get_mandatory_float, "Entry Price")
                        size = session.prompt("size", get_mandatory_float, "Size")
                        tp = session.prompt("tp", get_mandatory_float, "Take Profit")
                        entry_time = session.prompt("entry_time", get_mandatory_datetime, "Entry Time")

                        def ask_gates():
                            choices = [
                                Choice("g1", name="G1: Trend 15m"),
                                Choice("g2", name="G2: Fractal Trend"),
                                Choice("g3", name="G3: Limit Order"),
                                Choice("g4", name="G4: Breathing"),
                                Choice("g5", name="G5: Manual Cooldown"),
                                Choice("g6", name="G6: SL Validated"),
                                Choice("g7", name="G7: TP Validated")
                            ]
                            return bind_pause(inquirer.checkbox(message="Select fulfilled Gates >", choices=choices)).execute()
                            
                        selected_gates = session.prompt("selected_gates", ask_gates)
                        gates_failed_cnt = 7 - len(selected_gates)
                        
                        g1_trend_15m = "g1" in selected_gates
                        g2_fractal_trend = "g2" in selected_gates
                        g3_limit_order = "g3" in selected_gates
                        g4_breathing = "g4" in selected_gates
                        g5_manual_cooldown = "g5" in selected_gates
                        g6_sl_validated = "g6" in selected_gates
                        g7_tp_validated = "g7" in selected_gates

                        abort_trade = False
                        if gates_failed_cnt > 0:
                            gate_action = session.prompt("gate_action", lambda: bind_pause(inquirer.select(
                                message="[GATES FAILED] Operación inválida estructuralmente:",
                                choices=["Abortar Trade", "Forzar Entrada (Revenge)"]
                            )).execute())
                            if gate_action == "Abortar Trade":
                                abort_trade = True
                                t_status = next((c for c in TradeStatus if c.value == "Trade_no_taken"), TradeStatus.NO_TAKEN)
                                session.state["t_status"] = t_status
                        
                        if abort_trade:
                            t_comp = session.prompt("t_comp", get_enum_choice, "Compliance State", ComplianceState)
                            htf_trend = session.prompt("htf_trend", get_enum_choice, "HTF Trend Context", HTFTrendContext)
                            ltf_trend = session.prompt("ltf_trend", get_enum_choice, "LTF Trend Context", TrendContext)
                            lesson_tact = session.prompt("lesson_tact", get_mandatory_text, "Tactical Lesson Learned", multiline=True)
                            visual_path = session.prompt("visual_lesson_path", handle_visual_lesson_assignment, trade_id, payload.get("asset", "Unknown"))
                            
                            audit_tactical = TacticalAudit(
                                tactical_id=trade_id,
                                trade_status=t_status,
                                compliance=t_comp,
                                htf_trend_context=htf_trend,
                                ltf_trend_context=ltf_trend,
                                lesson_learned=lesson_tact,
                                visual_lesson_path=visual_path,
                                gates_failed=gates_failed_cnt,
                                confirmations_count=0,
                                stop_loss=0.0,
                                entry_price=0.0,
                                size=0.0,
                                take_profit=0.0,
                                cost=0.0,
                                mae=0.0,
                                mfe=0.0,
                                g1_trend_15m=g1_trend_15m,
                                g2_fractal_trend=g2_fractal_trend,
                                g3_limit_order=g3_limit_order,
                                g4_breathing=g4_breathing,
                                g5_manual_cooldown=g5_manual_cooldown,
                                g6_sl_validated=g6_sl_validated,
                                g7_tp_validated=g7_tp_validated
                            )
                        else:
                            if gates_failed_cnt == 0:
                                def ask_confirmations():
                                    choices = [
                                        Choice("c1", name="C1: KL Support"),
                                        Choice("c2", name="C2: Fractal Std"),
                                        Choice("c3", name="C3: Fractal 1m"),
                                        Choice("c4", name="C4: Fractal 1h"),
                                        Choice("c5", name="C5: KL Target"),
                                        Choice("c6", name="C6: Liquidity"),
                                        Choice("c7", name="C7: Retracement"),
                                        Choice("c8", name="C8: Convergence 15m")
                                    ]
                                    return bind_pause(inquirer.checkbox(message="Select fulfilled Confirmations >", choices=choices)).execute()
                                
                                selected_confs = session.prompt("selected_confs", ask_confirmations)
                                conf_status = session.prompt("conf_status", lambda: bind_pause(inquirer.select(
                                    message="Confirmation Status >",
                                    choices=[c for c in ConfirmationStatus if c not in (ConfirmationStatus.S7_REVENGE_FORCED, ConfirmationStatus.SKIP)]
                                )).execute())
                                
                                if conf_status == ConfirmationStatus.S6_FEAR_NO_ENTRY:
                                    mfe_potencial = session.prompt("mfe_potencial_estimado", get_mandatory_float, "MFE Potencial Estimado")
                                else:
                                    mfe_potencial = None
                            else:
                                selected_confs = []
                                conf_status = ConfirmationStatus.S7_REVENGE_FORCED
                                mfe_potencial = None
                                
                            confirmations_count = len(selected_confs)
                            c1_kl_support = "c1" in selected_confs
                            c2_fractal_std = "c2" in selected_confs
                            c3_fractal_1m = "c3" in selected_confs
                            c4_fractal_1h = "c4" in selected_confs
                            c5_kl_target = "c5" in selected_confs
                            c6_liquidity = "c6" in selected_confs
                            c7_retracement = "c7" in selected_confs
                            c8_convergence_15m = "c8" in selected_confs

                            if conf_status == ConfirmationStatus.S7_REVENGE_FORCED or gates_failed_cnt >= 3:
                                tier_setup = TierSetup.F
                            elif gates_failed_cnt >= 1:
                                tier_setup = TierSetup.D
                            elif confirmations_count >= 5:
                                tier_setup = TierSetup.A
                            elif confirmations_count >= 4:
                                tier_setup = TierSetup.B
                            else:
                                tier_setup = TierSetup.C

                            htf_trend = session.prompt("htf_trend", get_enum_choice, "HTF Trend Context", HTFTrendContext)
                            ltf_trend = session.prompt("ltf_trend", get_enum_choice, "LTF Trend Context", TrendContext)
                            emotions = session.prompt("emotions", get_multi_enum_choice, "Emotions", Emotions)
                            pre_trade_emotions = session.prompt("pre_trade_emotions", get_mandatory_text, "Pre Trade Emotions")
                            p_emotion = session.prompt("p_emotion", get_enum_choice, "Primary Emotion", PrimaryEmotion)
                            mental_clarity = session.prompt("mental_clarity", get_mandatory_int, "Mental Clarity Level", 1, 5)
                            impatience = session.prompt("impatience", get_mandatory_int, "Impatience Level", 1, 5)
                            anxiety = session.prompt("anxiety", get_mandatory_int, "Anxiety Level", 1, 5)
                            mid_trade_emotions = session.prompt("mid_trade_emotions", get_mandatory_text, "Mid Trade Emotions")
                            post_trade_emotions = session.prompt("post_trade_emotions", get_mandatory_text, "Post Trade Emotions")
                            exit_time = session.prompt("exit_time", get_mandatory_datetime, "Exit Time")
                            exit_type = session.prompt("exit_type", get_enum_choice, "Exit Type", ExitType)
                            close_p = session.prompt("close_p", get_mandatory_float, "Closing Price")
                            
                            def ask_could_hit_tp():
                                return bind_pause(inquirer.select(
                                    message="Could hit TP? >",
                                    choices=[Choice("yes", name="yes"), Choice("no", name="no")],
                                    pointer=">",
                                    qmark="",
                                    keybindings={"skip": []}
                                )).execute()
                                
                            could_hit_tp = session.prompt("could_hit_tp", ask_could_hit_tp)
                            t_comp = session.prompt("t_comp", get_enum_choice, "Compliance State", ComplianceState)
                            market_state = session.prompt("market_state", get_enum_choice, "Market State", MarketState)
                            f_plan = session.prompt("f_plan", get_enum_choice, "Followed Plan", FollowedPlan)
                            setup_t = session.prompt("setup_t", get_enum_choice, "Setup Type", SetupType)
                            behav_errors = session.prompt("behav_errors", get_multi_enum_choice, "Behavioral Errors", BehavioralErrors)
                            cog_patterns = session.prompt("cog_patterns", get_multi_enum_choice, "Cognitive Patterns", CognitivePatterns)
                            mae = session.prompt("mae", get_mandatory_float, "MAE (0 <= MAE <= 10)", min_val=0, max_val=10)
                            mfe = session.prompt("mfe", get_mandatory_float, "MFE (0 <= MFE <= 10)", min_val=0, max_val=10)
                            cost = session.prompt("cost", get_mandatory_float, "Cost (Fees/Funding)")
                            lesson_tact = session.prompt("lesson_tact", get_mandatory_text, "Tactical Lesson Learned", multiline=True)
                            visual_path = session.prompt("visual_lesson_path", handle_visual_lesson_assignment, trade_id, payload.get("asset", "Unknown"))

                            audit_tactical = TacticalAudit(
                                tactical_id=trade_id,
                                trade_status=t_status,
                                htf_trend_context=htf_trend,
                                ltf_trend_context=ltf_trend,
                                stop_loss=sl,
                                entry_price=entry_p,
                                size=size,
                                take_profit=tp,
                                entry_time=entry_time,
                                emotions=emotions,
                                pre_trade_emotions=pre_trade_emotions,
                                primary_emotion=p_emotion,
                                mental_clarity_level=mental_clarity,
                                impatience_level=impatience,
                                anxiety_level=anxiety,
                                mid_trade_emotions=mid_trade_emotions,
                                post_trade_emotions=post_trade_emotions,
                                exit_time=exit_time,
                                exit_type=exit_type,
                                confirmation_status=conf_status,
                                closing_price=close_p,
                                could_hit_tp=could_hit_tp,
                                compliance=t_comp,
                                tier_setup=tier_setup,
                                market_state=market_state,
                                followed_plan=f_plan,
                                setup_type=setup_t,
                                behavioral_errors=behav_errors,
                                cognitive_patterns=cog_patterns,
                                cost=cost,
                                mae=mae,
                                mfe=mfe,
                                lesson_learned=lesson_tact,
                                visual_lesson_path=visual_path,
                                g1_trend_15m=g1_trend_15m,
                                g2_fractal_trend=g2_fractal_trend,
                                g3_limit_order=g3_limit_order,
                                g4_breathing=g4_breathing,
                                g5_manual_cooldown=g5_manual_cooldown,
                                g6_sl_validated=g6_sl_validated,
                                g7_tp_validated=g7_tp_validated,
                                c1_kl_support=c1_kl_support,
                                c2_fractal_std=c2_fractal_std,
                                c3_fractal_1m=c3_fractal_1m,
                                c4_fractal_1h=c4_fractal_1h,
                                c5_kl_target=c5_kl_target,
                                c6_liquidity=c6_liquidity,
                                c7_retracement=c7_retracement,
                                c8_convergence_15m=c8_convergence_15m,
                                gates_failed=gates_failed_cnt,
                                confirmations_count=confirmations_count,
                                mfe_potencial_estimado=mfe_potencial
                            )
                        if True:
"""

if search_str in content:
    content = content.replace(search_str, replacement_str)
    with open("cli/main.py", "w") as f:
        f.write(content)
    print("Success")
else:
    print("String not found in cli/main.py. Dumping debug:")
    with open("cli/main_debug.txt", "w") as f:
        f.write(content[25000:30000]) # just somewhere
