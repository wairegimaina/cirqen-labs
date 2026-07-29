# Auto-generated refactor of the original Cirqen main.py application orchestration layer.
from __future__ import annotations

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .runtime import *
from .database import FirstRunSetup
from .services import ServiceManager, ServiceThread
from .setup_ui import SetupDialog, SetupThread, _themed_dialog, perform_startup_cleanup
from .ui import CustomSplashScreen, MainWindow
from sync.startup_warmup import StartupStateManager

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon
from PySide6.QtCore import QTimer, QUrl


def main():
    """
    ENHANCED: Main entry point with automatic cleanup, dynamic port allocation,
    and update checking
    """
    import multiprocessing as _mp

    try:
        _mp.set_start_method("spawn", force=True)
        logger.info("multiprocessing start method set to 'spawn'")
    except RuntimeError:
        pass

    app = QApplication(sys.argv)
    app.setApplicationName("Cirqen")
    app.setOrganizationName("B12 Technologies")

    icon_path = APPLICATION_PATH / "assets" / "icons" / "app_icon.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    logger.info("=" * 70)
    logger.info("🚀 CIRQEN APPLICATION - ENHANCED EDITION")
    logger.info("=" * 70)
    logger.info(f"Platform: {sys.platform}")
    logger.info(f"Python: {sys.version.split()[0]}")
    logger.info(f"PID: {os.getpid()}")
    logger.info(f"Data Path: {DATA_PATH}")
    logger.info("=" * 70)

    logger.info("STEP 1: Running startup cleanup...")
    perform_startup_cleanup()

    logger.info("STEP 2: Allocating ports...")
    try:
        port_manager.allocate_ports()
    except RuntimeError as e:
        logger.error(f"❌ Port allocation failed: {e}")
        return 1

    instance_lock = None
    if not should_skip_instance_lock():
        LOCK_FILE = DATA_PATH / "cirqen.lock"
        instance_lock = SingleInstanceLock(LOCK_FILE, port_manager)
        lock_success, lock_message = instance_lock.acquire()
        if not lock_success:
            logger.error(f"❌ Instance lock failed: {lock_message}")
            return 1
        logger.info("✅ Instance lock acquired")
    else:
        logger.info("⚠️  Skipping instance lock (subprocess/migration mode)")

    # The lock above just confirmed we're the only Cirqen instance, so
    # anything matching here is a leftover from a previous session that
    # died without cleaning up (crash / kill -9 / power loss) rather than
    # exiting normally.
    try:
        kill_orphaned_service_processes()
    except Exception as _sweep_error:
        logger.warning(f"Orphan process sweep skipped: {_sweep_error}")

    service_manager = None  # bound below once created; referenced here so
                             # cleanup_on_exit() can stop child processes
                             # (Postgres/Redis/Celery/Django) on ANY exit
                             # path, not just a graceful window-close.

    def cleanup_on_exit():
        logger.info("=" * 70)
        logger.info("CLEANUP ON EXIT")
        logger.info("=" * 70)

        # Without this, SIGTERM/SIGINT/crash exits (anything that skips
        # MainWindow.closeEvent's confirmation dialog) leave Postgres-HQ,
        # Redis, Celery, and the Django subprocess running as orphans —
        # observed in practice as redis-server instances stacking up across
        # restarts, each still holding its port.
        if service_manager is not None:
            try:
                service_manager.stop_services()
            except Exception as stop_error:
                logger.error(f"Error stopping services during cleanup: {stop_error}")

        if instance_lock:
            instance_lock.release()
        port_manager.cleanup_session()
        logger.info("✅ Cleanup complete")

    atexit.register(cleanup_on_exit)

    def signal_handler(sig, frame):
        msg = f"\nReceived signal {sig} — shutting down...\n".encode()
        os.write(2, msg)
        raise SystemExit(0)

    signal.signal(signal.SIGINT, signal_handler)
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, signal_handler)

    try:
        splash = CustomSplashScreen(port_manager)
        splash.show()
        app.processEvents()

        try:
            logger.info("🚀 Initiating startup warmup sequence...")
            startup_manager = StartupStateManager(DATA_PATH, logger)
            last_startup = startup_manager.get_last_startup_info()
            if last_startup:
                last_time = last_startup.get("last_startup", "Unknown")
                last_cleared = last_startup.get("cleared_items", 0)
                logger.info(f"📅 Last startup: {last_time}")
                logger.info(f"📊 Last session cleared: {last_cleared} items")

            splash.update_progress("Getting things ready…", 5)
            app.processEvents()
            cleared_count = startup_manager.clear_stale_states()

            def warmup_progress_callback(step_name, progress):
                splash_progress = 10 + int((progress / 100) * 30)
                splash.update_progress("Optimising your experience…", splash_progress)
                app.processEvents()

            startup_manager.warmup_application(progress_callback=warmup_progress_callback)
            startup_manager.save_startup_state()
            splash.update_progress("Almost there…", 45)
            app.processEvents()

            logger.info("=" * 70)
            logger.info(f"✅ STARTUP WARMUP COMPLETE")
            logger.info(f"   • Cleared: {cleared_count} stale items")
            logger.info(f"   • Warnings: {len(startup_manager.warnings)}")
            logger.info("=" * 70)
        except Exception as e:
            logger.warning(f"⚠️  Startup warmup encountered an issue: {e}")
            logger.info("Continuing with normal startup...")
            import traceback

            logger.debug(traceback.format_exc())

        first_run_setup = FirstRunSetup(port_manager)

        if first_run_setup.is_first_run():
            logger.info("STEP 3: First run detected - showing setup dialog")
            setup_dialog = SetupDialog()
            setup_thread = SetupThread(first_run_setup)
            first_run_setup.progress_update.connect(setup_dialog.update_progress)
            first_run_setup.log_message.connect(setup_dialog.add_log)
            setup_complete = [False, None]

            def on_setup_done(success, message):
                setup_complete[0] = success
                setup_complete[1] = message
                setup_dialog.accept()

            first_run_setup.setup_complete.connect(on_setup_done)
            setup_thread.start()
            setup_dialog.exec()
            setup_thread.wait()

            if not setup_complete[0]:
                splash.close()
                _themed_dialog(
                    "error",
                    "Setup didn’t complete",
                    "Something went wrong during the initial setup.",
                    f"Details: {setup_complete[1]}",
                )
                logger.error("❌ First-run setup failed")
                cleanup_on_exit()
                return 1

            splash.close()
            _themed_dialog(
                "info",
                "You’re all set!",
                "Cirqen is ready to use. Your workspace has been created.",
                setup_complete[1],
            )
            logger.info("✅ First-run setup completed")

            splash = CustomSplashScreen(port_manager)
            splash.show()
            app.processEvents()

        logger.info("STEP 4: Starting services...")
        service_manager = ServiceManager(port_manager)
        service_manager.progress_update.connect(splash.update_progress)
        service_thread = ServiceThread(service_manager)
        main_window = MainWindow(port_manager, service_manager)

        def on_ready():
            logger.info("=" * 70)
            logger.info("🎉 ALL SERVICES READY - WARMING UP DJANGO")
            logger.info("=" * 70)
            splash.update_progress("You’re almost in…", 95)

            def show_window_when_ready():
                logger.info("🔔 Django warmed up - showing main window...")
                screen = QApplication.primaryScreen()
                screen_geometry = screen.availableGeometry()
                main_window.setGeometry(screen_geometry)
                splash.finish(main_window)
                main_window.showMaximized()

                django_url = f"http://127.0.0.1:{main_window.django_port}/"
                logger.info(f"Loading URL: {django_url}")
                main_window.web_view.setUrl(QUrl(django_url))
                main_window.refresh_btn.setEnabled(True)
                main_window.status_label.setText("🟢 System Online")
                # Wire AppUpdateService signals now that both window and
                # service exist.  update_status_timer kept for compat but
                # not started — signals drive updates instead.
                if service_manager._update_manager is not None:
                    main_window.connect_update_service(service_manager._update_manager)
                    logger.info("✅ AppUpdateService wired to MainWindow signals")
                main_window.sync_status_timer.start(5000)
                QTimer.singleShot(2000, main_window.update_sync_online_indicator)
                logger.info("✅ Application ready and displayed")

            QTimer.singleShot(5000, show_window_when_ready)

        def on_error(error):
            splash.close()
            logger.error(f"❌ Service error: {error}")
            _themed_dialog(
                "error",
                "Couldn’t start Cirqen",
                "One of the background services failed to start.",
                f"Details: {error}",
            )
            cleanup_on_exit()
            app.quit()

        service_manager.service_ready.connect(on_ready)
        service_manager.service_error.connect(on_error)
        service_thread.start()

        logger.info("STEP 5: Starting Qt event loop...")
        exit_code = app.exec()
        logger.info(f"Application exiting (code={exit_code})")

        restart_requested = False
        try:
            sentinel_candidates = []
            sentinel_candidates.append(DATA_PATH / ".restart_required")
            sentinel_candidates.append(APPLICATION_PATH / ".restart_required")
            sentinel_candidates.append(APPLICATION_PATH / "_internal" / ".restart_required")
            sentinel_candidates.append(Path(__file__).resolve().parent / ".restart_required")

            for restart_sentinel in sentinel_candidates:
                if restart_sentinel.exists():
                    restart_requested = True
                    restart_sentinel.unlink(missing_ok=True)
                    break
        except Exception as sentinel_error:
            logger.warning(f"Restart sentinel check failed: {sentinel_error}")

        if restart_requested:
            logger.info("Restart requested — stopping services before relaunch")
            try:
                # CRITICAL: stop_services() must run on the restart path too.
                # Without this, the old non-daemon Django multiprocessing
                # Process (and Celery/sync-agent) is left running in the
                # background after the new process starts. The new window
                # looks fresh (new Qt process, new splash, new webview) but
                # the OLD Django process — still holding the previous
                # in-memory module state from before the update — may still
                # be alive on its old port, and Celery/sync-agent workers
                # keep executing old code against shared DB/Redis state.
                # This is what causes "frontend updates fine, backend doesn't".
                service_manager.stop_services()
                service_thread.wait(timeout=15000)
            except Exception as stop_error:
                logger.error(f"Error stopping services before restart: {stop_error}")

            logger.info("Relaunching application")
            try:
                restart_cmd, use_shell, cwd, env = get_restart_command()
                subprocess.Popen(
                    restart_cmd,
                    cwd=cwd,
                    shell=use_shell,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except Exception as restart_error:
                logger.error(f"Restart failed: {restart_error}")
            return 0

        service_manager.stop_services()
        service_thread.wait()
        return exit_code

    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        import traceback

        logger.error(traceback.format_exc())
        _themed_dialog(
            "error",
            "Something went wrong",
            "Cirqen encountered an unexpected problem and needs to close.",
            f"Details: {str(e)}",
        )
        return 1
    finally:
        cleanup_on_exit()
