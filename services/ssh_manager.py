"""
SSH connection and server management utilities (Async)
"""
import asyncssh
import asyncio
import os
from typing import Optional, Tuple, List, Dict, Any
from datetime import datetime
from modules.models import Server, AuthType
from services.server_monitor import server_monitor


class SSHManager:
    """Async SSH manager for remote server operations"""
    
    # Constants for file validation
    MIN_EXPECTED_FILE_SIZE = 1000  # Minimum file size in bytes (1KB) for downloaded packages
    
    def __init__(self):
        self.conn: Optional[asyncssh.SSHClientConnection] = None
    
    async def connect(self, server: Server) -> Tuple[bool, str]:
        """
        Connect to server via SSH
        Returns: (success: bool, message: str)
        """
        try:
            if server.auth_type == AuthType.PASSWORD:
                # Password authentication
                self.conn = await asyncssh.connect(
                    host=server.host,
                    port=server.ssh_port,
                    username=server.ssh_user,
                    password=server.ssh_password,
                    known_hosts=None
                )
            elif server.auth_type == AuthType.KEY_FILE:
                # Key file authentication
                self.conn = await asyncssh.connect(
                    host=server.host,
                    port=server.ssh_port,
                    username=server.ssh_user,
                    client_keys=[server.ssh_key_path],
                    known_hosts=None
                )
            else:
                return False, f"Unsupported auth type: {server.auth_type}"
            
            return True, "Connected successfully"
        except asyncssh.PermissionDenied:
            return False, "Authentication failed"
        except asyncssh.Error as e:
            return False, f"SSH error: {str(e)}"
        except Exception as e:
            return False, f"Connection error: {str(e)}"
    
    async def execute_command(self, command: str, timeout: int = 30) -> Tuple[bool, str, str]:
        """
        Execute command on remote server
        Returns: (success: bool, stdout: str, stderr: str)
        """
        if not self.conn:
            return False, "", "Not connected"
        
        try:
            result = await asyncio.wait_for(
                self.conn.run(command, check=False),
                timeout=timeout
            )
            
            stdout_text = result.stdout
            stderr_text = result.stderr
            exit_status = result.exit_status
            
            return exit_status == 0, stdout_text, stderr_text
        except asyncio.TimeoutError:
            return False, "", "Command timeout"
        except Exception as e:
            return False, "", str(e)
    
    async def execute_command_streaming(self, command: str, output_callback=None, timeout: int = 1800) -> Tuple[bool, str, str]:
        """
        Execute command on remote server with real-time output streaming
        
        Args:
            command: Command to execute
            output_callback: Optional async callback function to receive output lines in real-time
            timeout: Command timeout in seconds (default: 1800 = 30 minutes)
        
        Returns: (success: bool, stdout: str, stderr: str)
        """
        if not self.conn:
            return False, "", "Not connected"
        
        stdout_lines = []
        stderr_lines = []
        
        async def _execute():
            # Create the process
            process = await self.conn.create_process(command)
            
            # Helper to send output via callback
            async def send_output(line: str):
                if output_callback:
                    if asyncio.iscoroutinefunction(output_callback):
                        await output_callback(line)
                    else:
                        output_callback(line)
            
            # Read stdout and stderr concurrently
            async def read_stream(stream, lines_list, prefix=""):
                """Read from a stream and collect lines"""
                try:
                    async for line in stream:
                        line = line.rstrip('\n\r')
                        if line:  # Only process non-empty lines
                            lines_list.append(line)
                            # Send to callback with prefix
                            await send_output(f"{prefix}{line}" if prefix else line)
                except Exception as e:
                    await send_output(f"Stream read error: {str(e)}")
            
            # Read both stdout and stderr concurrently
            await asyncio.gather(
                read_stream(process.stdout, stdout_lines),
                read_stream(process.stderr, stderr_lines, "[STDERR] "),
                return_exceptions=True
            )
            
            # Wait for process to complete
            exit_status = await process.wait()
            
            stdout_text = '\n'.join(stdout_lines)
            stderr_text = '\n'.join(stderr_lines)
            
            return exit_status == 0, stdout_text, stderr_text
        
        try:
            return await asyncio.wait_for(_execute(), timeout=timeout)
        except asyncio.TimeoutError:
            return False, '\n'.join(stdout_lines), "Command timeout"
        except Exception as e:
            return False, '\n'.join(stdout_lines), f"Execution error: {str(e)}"
    
    async def execute_sudo_command(self, command: str, sudo_password: Optional[str] = None, 
                                   timeout: int = 30) -> Tuple[bool, str, str]:
        """
        Execute command with sudo on remote server
        Returns: (success: bool, stdout: str, stderr: str)
        """
        if not self.conn:
            return False, "", "Not connected"
        
        try:
            if sudo_password:
                # Use -S option to read password from stdin
                full_command = f"echo '{sudo_password}' | sudo -S {command}"
            else:
                # Try passwordless sudo
                full_command = f"sudo {command}"
            
            result = await asyncio.wait_for(
                self.conn.run(full_command, check=False),
                timeout=timeout
            )
            
            stdout_text = result.stdout
            stderr_text = result.stderr
            exit_status = result.exit_status
            
            return exit_status == 0, stdout_text, stderr_text
        except asyncio.TimeoutError:
            return False, "", "Command timeout"
        except Exception as e:
            return False, "", str(e)
    
    async def disconnect(self):
        """Close SSH connection"""
        if self.conn:
            self.conn.close()
            await self.conn.wait_closed()
            self.conn = None
    
    async def deploy_cs2_server(self, server: Server, progress_callback=None) -> Tuple[bool, str]:
        """
        Deploy CS2 server on Ubuntu 24.04+ without requiring sudo
        Similar to LinuxGSM approach - works entirely in user space
        
        Prerequisites (must be installed by system administrator):
        - lib32gcc-s1, lib32stdc++6, curl, wget, tar, screen, unzip
        
        Args:
            server: Server instance
            progress_callback: Optional async callback for progress updates
        Returns: (success: bool, message: str)
        """
        async def send_progress(message: str):
            """Helper to send progress updates"""
            if progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
                    await progress_callback(message)
                else:
                    progress_callback(message)
        
        success, msg = await self.connect(server)
        if not success:
            return False, f"Connection failed: {msg}"
        
        try:
            # Check if environment is initialized (cs2server user exists)
            await send_progress("Checking environment initialization...")
            
            # Check if game_directory path suggests we need cs2server user
            if '/home/cs2server' in server.game_directory:
                # Check if cs2server user exists
                check_user_cmd = "id cs2server > /dev/null 2>&1 && echo 'exists' || echo 'missing'"
                user_success, user_stdout, _ = await self.execute_command(check_user_cmd)
                
                if 'missing' in user_stdout or not user_success:
                    await send_progress("✗ Environment not initialized: cs2server user does not exist")
                    return False, (
                        "Environment not initialized. Please create cs2server user first:\n"
                        "sudo useradd -m -s /bin/bash cs2server\n"
                        "sudo passwd cs2server\n"
                        "sudo usermod -aG sudo cs2server  # Optional: for installing dependencies\n\n"
                        "Or use a different game_directory path that the current user can access."
                    )
                
                await send_progress("✓ cs2server user exists")
                
                # Verify cs2server home directory has correct permissions
                check_perms_cmd = "test -w /home/cs2server && echo 'writable' || echo 'not_writable'"
                perm_success, perm_stdout, _ = await self.execute_command(check_perms_cmd)
                
                if 'not_writable' in perm_stdout or not perm_success:
                    await send_progress("✗ /home/cs2server directory is not writable")
                    
                    # Try to fix permissions if we have sudo password
                    if server.sudo_password:
                        await send_progress("Attempting to fix permissions...")
                        fix_perms_cmd = f"echo '{server.sudo_password}' | sudo -S chown -R cs2server:cs2server /home/cs2server && echo '{server.sudo_password}' | sudo -S chmod 755 /home/cs2server"
                        fix_success, _, fix_stderr = await self.execute_command(fix_perms_cmd)
                        
                        if fix_success:
                            await send_progress("✓ Permissions fixed for /home/cs2server")
                        else:
                            return False, (
                                f"Cannot create directory in /home/cs2server: Permission denied.\n"
                                f"Please ensure the directory has correct permissions:\n"
                                f"sudo chown -R cs2server:cs2server /home/cs2server\n"
                                f"sudo chmod 755 /home/cs2server"
                            )
                    else:
                        return False, (
                            "Cannot create directory in /home/cs2server: Permission denied.\n"
                            "Please ensure the directory has correct permissions:\n"
                            "sudo chown -R cs2server:cs2server /home/cs2server\n"
                            "sudo chmod 755 /home/cs2server"
                        )
                else:
                    await send_progress("✓ /home/cs2server is writable")
            
            # Check if required tools are available
            await send_progress("Checking system prerequisites...")
            required_tools = ["wget", "tar", "screen", "unzip"]
            missing_tools = []
            for tool in required_tools:
                success, stdout, stderr = await self.execute_command(f"command -v {tool}")
                if not success:
                    await send_progress(f"⚠ Warning: {tool} not found")
                    missing_tools.append(tool)
                else:
                    await send_progress(f"✓ Found {tool}: {stdout.strip()}")
            
            # Try to install missing tools
            if missing_tools:
                await send_progress(f"Attempting to install missing tools: {', '.join(missing_tools)}")
                # Check package manager
                check_apt = "command -v apt-get > /dev/null && echo 'apt' || echo 'none'"
                _, pkg_mgr, _ = await self.execute_command(check_apt)
                
                if 'apt' in pkg_mgr:
                    # Try to install without sudo first (user might have passwordless sudo)
                    install_cmd = f"apt-get update && apt-get install -y {' '.join(missing_tools)}"
                    success, stdout, stderr = await self.execute_command(install_cmd, timeout=120)
                    
                    if not success:
                        # Try with sudo
                        if server.sudo_password:
                            await send_progress("Trying to install with sudo...")
                            install_cmd = f"echo '{server.sudo_password}' | sudo -S apt-get update && echo '{server.sudo_password}' | sudo -S apt-get install -y {' '.join(missing_tools)}"
                            success, stdout, stderr = await self.execute_command(install_cmd, timeout=120)
                            
                            if success:
                                await send_progress(f"✓ Successfully installed: {', '.join(missing_tools)}")
                            else:
                                await send_progress(f"⚠ Could not install tools. Please run: sudo apt-get install {' '.join(missing_tools)}")
                        else:
                            await send_progress(f"⚠ Could not install tools (no sudo password). Please run: sudo apt-get install {' '.join(missing_tools)}")
                    else:
                        await send_progress(f"✓ Successfully installed: {', '.join(missing_tools)}")
                else:
                    await send_progress(f"⚠ Please manually install: {', '.join(missing_tools)}")
            
            # Create directory
            await send_progress(f"Creating game directory: {server.game_directory}")
            success, stdout, stderr = await self.execute_command(f"mkdir -p {server.game_directory}")
            if not success:
                return False, f"Directory creation failed: {stderr}"
            await send_progress(f"✓ Game directory created successfully")
            
            # Download and install SteamCMD
            steamcmd_dir = f"{server.game_directory}/steamcmd"
            await send_progress("Setting up SteamCMD...")
            
            # Create SteamCMD directory
            await send_progress("Creating SteamCMD directory...")
            success, stdout, stderr = await self.execute_command(f"mkdir -p {steamcmd_dir}")
            if not success:
                return False, f"SteamCMD directory creation failed: {stderr}"
            await send_progress(f"✓ SteamCMD directory created")
            
            # Download SteamCMD with streaming output
            await send_progress("Downloading SteamCMD...")
            download_cmd = f"wget --progress=dot:mega https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz -O {steamcmd_dir}/steamcmd_linux.tar.gz"
            success, stdout, stderr = await self.execute_command_streaming(
                download_cmd,
                output_callback=send_progress,
                timeout=300
            )
            if not success:
                # Check if file was downloaded successfully despite non-zero exit code
                check_cmd = f"test -f {steamcmd_dir}/steamcmd_linux.tar.gz && echo 'exists'"
                check_success, check_stdout, _ = await self.execute_command(check_cmd)
                if not check_success or 'exists' not in check_stdout:
                    return False, f"SteamCMD download failed: {stderr if stderr else 'Download incomplete'}"
                # File exists, continue despite wget exit code
                await send_progress(f"✓ SteamCMD download completed (file verified)")
            else:
                await send_progress(f"✓ SteamCMD downloaded successfully")
            
            # Extract SteamCMD
            await send_progress("Extracting SteamCMD...")
            success, stdout, stderr = await self.execute_command(
                f"tar -xzf {steamcmd_dir}/steamcmd_linux.tar.gz -C {steamcmd_dir}",
                timeout=120
            )
            if not success:
                return False, f"SteamCMD extraction failed: {stderr}"
            await send_progress(f"✓ SteamCMD extracted successfully")
            
            # Install CS2 server (App ID: 730) with streaming output
            await send_progress("=" * 60)
            await send_progress("Installing CS2 server via SteamCMD...")
            await send_progress("This will download approximately 30GB and may take 15-30 minutes")
            await send_progress("Please be patient, you will see real-time progress below:")
            await send_progress("=" * 60)
            
            install_cs2 = (
                f"cd {steamcmd_dir} && "
                f"./steamcmd.sh "
                f"+force_install_dir {server.game_directory}/cs2 "
                f"+login anonymous "
                f"+app_update 730 validate "
                f"+quit"
            )
            
            # Display command preview before execution
            await send_progress("")
            await send_progress("即将执行的命令 / Commands to be executed:")
            await send_progress("=" * 60)
            await send_progress(f"📝 SteamCMD Install Command:")
            await send_progress(f"   {install_cs2}")
            await send_progress("=" * 60)
            await send_progress("")
            
            success, stdout, stderr = await self.execute_command_streaming(
                install_cs2,
                output_callback=send_progress,
                timeout=1800  # 30 minutes
            )
            
            if not success:
                # SteamCMD may restart itself during updates, which can cause non-zero exit codes
                # Check if CS2 was actually installed successfully
                await send_progress("Verifying CS2 installation...")
                verify_cmd = f"test -f {server.game_directory}/cs2/game/bin/linuxsteamrt64/cs2 && echo 'installed'"
                verify_success, verify_stdout, _ = await self.execute_command(verify_cmd)
                
                if not verify_success or 'installed' not in verify_stdout:
                    await send_progress(f"CS2 installation failed!")
                    await send_progress(f"Error details: {stderr}")
                    return False, f"CS2 installation failed: {stderr if stderr else 'Installation incomplete'}"
            
            # Fix steamclient.so symlink issue (required for CS2 to start)
            # See: https://developer.valvesoftware.com/wiki/Counter-Strike_2/Dedicated_Servers#Troubleshooting
            await send_progress("=" * 60)
            await send_progress("Fixing steamclient.so symlink (required for server startup)...")
            await send_progress("=" * 60)
            
            # Create ~/.steam/sdk64 directory if it doesn't exist
            steam_sdk_dir = f"/home/{server.ssh_user}/.steam/sdk64"
            mkdir_cmd = f"mkdir -p {steam_sdk_dir}"
            await self.execute_command(mkdir_cmd)
            
            # Create symlink to steamclient.so
            # This fixes: "Failed to load module '/home/user/.steam/sdk64/steamclient.so'"
            steamclient_source = f"{steamcmd_dir}/linux64/steamclient.so"
            steamclient_target = f"{steam_sdk_dir}/steamclient.so"
            symlink_cmd = f"ln -sf {steamclient_source} {steamclient_target}"
            symlink_success, _, _ = await self.execute_command(symlink_cmd)
            
            if symlink_success:
                await send_progress("✓ steamclient.so symlink created successfully")
            else:
                await send_progress("⚠ Warning: Could not create steamclient.so symlink (may cause startup issues)")
                
                # CS2 executable exists, installation successful despite exit code
                await send_progress("=" * 60)
                await send_progress("✓ CS2 server installed successfully (verified)")
                await send_progress("=" * 60)
                return True, "CS2 server deployed successfully"
            
            await send_progress("=" * 60)
            await send_progress("✓ CS2 server installed successfully!")
            await send_progress("=" * 60)
            
            # Deploy auto-restart wrapper script
            await send_progress("=" * 60)
            await send_progress("Deploying auto-restart wrapper script...")
            await send_progress("=" * 60)
            
            autorestart_script_path = f"{server.game_directory}/cs2_autorestart.sh"
            
            # Read the autorestart script content
            import os
            script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            local_script_path = os.path.join(script_dir, "scripts", "cs2_autorestart.sh")
            
            try:
                with open(local_script_path, 'r') as f:
                    script_content = f.read()
                
                # Create the script on remote server
                create_script_cmd = f"cat > {autorestart_script_path} << 'EOFSCRIPT'\n{script_content}\nEOFSCRIPT"
                success, stdout, stderr = await self.execute_command(create_script_cmd, timeout=10)
                
                if not success:
                    await send_progress(f"⚠ Warning: Could not deploy autorestart script: {stderr}")
                else:
                    # Make script executable
                    chmod_script_cmd = f"chmod +x {autorestart_script_path}"
                    await self.execute_command(chmod_script_cmd)
                    await send_progress("✓ Auto-restart wrapper script deployed successfully")
            except Exception as e:
                await send_progress(f"⚠ Warning: Could not deploy autorestart script: {str(e)}")
            
            await send_progress("=" * 60)
            await send_progress("✓ Deployment completed successfully!")
            await send_progress("=" * 60)
            
            return True, "CS2 server deployed successfully"
        
        except Exception as e:
            await send_progress(f"Deployment error: {str(e)}")
            return False, f"Deployment error: {str(e)}"
        finally:
            await self.disconnect()
    
    async def perform_server_selfcheck(self, server: Server, progress_callback=None) -> Tuple[bool, str]:
        """
        Perform universal self-checks on the CS2 server and automatically fix common issues.
        
        Checks performed:
        - CS2 executable exists and has proper permissions
        - steamclient.so symlink exists and is valid
        - gameinfo.gi is properly configured for Metamod (if installed)
        - Auto-restart script is deployed
        
        Args:
            server: Server instance
            progress_callback: Optional async callback for progress updates
        
        Returns:
            Tuple[bool, str]: (success, message)
        """
        async def send_progress(message: str):
            """Helper to send progress updates"""
            if progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
                    await progress_callback(message)
                else:
                    progress_callback(message)
        
        try:
            await send_progress("=" * 60)
            await send_progress("Performing server self-check and auto-fix...")
            await send_progress("=" * 60)
            
            issues_found = []
            issues_fixed = []
            
            # Check 1: CS2 executable exists and has proper permissions
            await send_progress("Checking CS2 executable...")
            cs2_executable = f"{server.game_directory}/cs2/game/bin/linuxsteamrt64/cs2"
            verify_cmd = f"test -f {cs2_executable} && echo 'exists'"
            verify_success, verify_stdout, _ = await self.execute_command(verify_cmd)
            
            if not verify_success or 'exists' not in verify_stdout:
                issues_found.append("CS2 executable not found")
                await send_progress("✗ CS2 executable not found - server may not be deployed")
            else:
                # Ensure executable has proper permissions
                chmod_cmd = f"chmod +x {cs2_executable}"
                chmod_success, _, _ = await self.execute_command(chmod_cmd)
                if chmod_success:
                    await send_progress("✓ CS2 executable found and permissions set")
                else:
                    await send_progress("⚠ CS2 executable found but could not set permissions")
            
            # Check 2: steamclient.so symlink
            await send_progress("Checking steamclient.so symlink...")
            steam_sdk_dir = f"/home/{server.ssh_user}/.steam/sdk64"
            steamclient_target = f"{steam_sdk_dir}/steamclient.so"
            
            check_cmd = f"test -L {steamclient_target} && test -e {steamclient_target} && echo 'valid' || echo 'missing'"
            check_success, check_stdout, _ = await self.execute_command(check_cmd)
            
            if 'missing' in check_stdout or not check_success:
                issues_found.append("steamclient.so symlink missing or broken")
                await send_progress("✗ steamclient.so symlink missing or broken - attempting to fix...")
                
                # Create directory
                mkdir_cmd = f"mkdir -p {steam_sdk_dir}"
                await self.execute_command(mkdir_cmd)
                
                # Find steamclient.so source
                steamcmd_dir = f"{server.game_directory}/steamcmd"
                steamclient_source = f"{steamcmd_dir}/linux64/steamclient.so"
                
                source_check = f"test -f {steamclient_source} && echo 'found' || echo 'notfound'"
                source_success, source_stdout, _ = await self.execute_command(source_check)
                
                if 'found' in source_stdout:
                    # Create symlink
                    symlink_cmd = f"ln -sf {steamclient_source} {steamclient_target}"
                    symlink_success, _, _ = await self.execute_command(symlink_cmd)
                    
                    if symlink_success:
                        issues_fixed.append("steamclient.so symlink")
                        await send_progress("✓ steamclient.so symlink created successfully")
                    else:
                        await send_progress("✗ Failed to create steamclient.so symlink")
                else:
                    await send_progress(f"✗ steamclient.so source not found at {steamclient_source}")
            else:
                await send_progress("✓ steamclient.so symlink is valid")
            
            # Check 3: gameinfo.gi for Metamod
            await send_progress("Checking gameinfo.gi configuration...")
            cs2_dir = f"{server.game_directory}/cs2"
            gameinfo_path = f"{cs2_dir}/game/csgo/gameinfo.gi"
            metamod_dir = f"{cs2_dir}/game/csgo/addons/metamod"
            
            # Check if Metamod is installed
            check_mm_cmd = f"test -d {metamod_dir} && echo 'exists'"
            mm_exists_success, mm_exists_stdout, _ = await self.execute_command(check_mm_cmd)
            
            if mm_exists_success and 'exists' in mm_exists_stdout:
                # Check if gameinfo.gi exists
                check_gi_cmd = f"test -f {gameinfo_path} && echo 'exists'"
                gi_exists_success, gi_exists_stdout, _ = await self.execute_command(check_gi_cmd)
                
                if gi_exists_success and 'exists' in gi_exists_stdout:
                    # Check if Metamod is configured in gameinfo.gi
                    check_mm_line = f"grep -q 'addons/metamod' {gameinfo_path} && echo 'found' || echo 'notfound'"
                    check_line_success, check_line_stdout, _ = await self.execute_command(check_mm_line)
                    
                    if 'notfound' in check_line_stdout:
                        issues_found.append("Metamod not configured in gameinfo.gi")
                        await send_progress("✗ Metamod installed but not configured in gameinfo.gi - attempting to fix...")
                        
                        # Backup gameinfo.gi
                        backup_cmd = f"cp {gameinfo_path} {gameinfo_path}.backup.$(date +%Y%m%d_%H%M%S)"
                        await self.execute_command(backup_cmd)
                        
                        # Add Metamod to gameinfo.gi
                        sed_cmd = f"sed -i '/Game_LowViolence/a\\			Game\\tcsgo/addons/metamod' {gameinfo_path}"
                        sed_success, _, _ = await self.execute_command(sed_cmd)
                        
                        if sed_success:
                            issues_fixed.append("gameinfo.gi Metamod configuration")
                            await send_progress("✓ Metamod added to gameinfo.gi successfully")
                        else:
                            await send_progress("✗ Failed to update gameinfo.gi automatically")
                    else:
                        await send_progress("✓ Metamod is properly configured in gameinfo.gi")
                else:
                    await send_progress("⚠ gameinfo.gi not found (Metamod installed but game not deployed)")
            else:
                await send_progress("✓ Metamod not installed - gameinfo.gi check skipped")
            
            # Check 4: Auto-restart script
            await send_progress("Checking auto-restart script...")
            autorestart_script_path = f"{server.game_directory}/cs2_autorestart.sh"
            
            check_script_cmd = f"test -f {autorestart_script_path} && test -x {autorestart_script_path} && echo 'exists'"
            script_success, script_stdout, _ = await self.execute_command(check_script_cmd)
            
            if not script_success or 'exists' not in script_stdout:
                issues_found.append("Auto-restart script not found or not executable")
                await send_progress("✗ Auto-restart script missing - attempting to deploy...")
                
                # Deploy the script
                import os
                script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                local_script_path = os.path.join(script_dir, "scripts", "cs2_autorestart.sh")
                
                try:
                    with open(local_script_path, 'r') as f:
                        script_content = f.read()
                    
                    create_script_cmd = f"cat > {autorestart_script_path} << 'EOFSCRIPT'\n{script_content}\nEOFSCRIPT"
                    deploy_success, _, _ = await self.execute_command(create_script_cmd, timeout=10)
                    
                    if deploy_success:
                        chmod_script_cmd = f"chmod +x {autorestart_script_path}"
                        await self.execute_command(chmod_script_cmd)
                        issues_fixed.append("auto-restart script")
                        await send_progress("✓ Auto-restart script deployed successfully")
                    else:
                        await send_progress("✗ Failed to deploy auto-restart script")
                except Exception as e:
                    await send_progress(f"✗ Error deploying auto-restart script: {str(e)}")
            else:
                await send_progress("✓ Auto-restart script is deployed and executable")
            
            # Summary
            await send_progress("=" * 60)
            await send_progress("Self-check completed!")
            await send_progress("=" * 60)
            
            if issues_found:
                await send_progress(f"Issues found: {len(issues_found)}")
                for issue in issues_found:
                    await send_progress(f"  - {issue}")
            
            if issues_fixed:
                await send_progress(f"Issues fixed: {len(issues_fixed)}")
                for fix in issues_fixed:
                    await send_progress(f"  ✓ {fix}")
            
            if not issues_found:
                await send_progress("✓ No issues found - server is ready to start")
                return True, "Server self-check passed"
            elif len(issues_fixed) == len(issues_found):
                await send_progress("✓ All issues were automatically fixed")
                return True, "Server self-check completed with auto-fixes"
            else:
                unfixed = len(issues_found) - len(issues_fixed)
                await send_progress(f"⚠ {unfixed} issue(s) could not be automatically fixed")
                return False, f"{unfixed} issues remain"
        
        except Exception as e:
            await send_progress(f"Self-check error: {str(e)}")
            return False, f"Self-check error: {str(e)}"
    
    async def start_server(self, server: Server, progress_callback=None) -> Tuple[bool, str]:
        """
        Start CS2 server with LGSM-style configuration and real-time output streaming
        
        This method includes defensive checks to ensure no duplicate screen sessions.
        It will automatically terminate any existing screen session before starting a new one.
        This is critical for restart operations and prevents screen session conflicts.
        """
        success, msg = await self.connect(server)
        if not success:
            return False, f"Connection failed: {msg}"
        
        async def send_progress(message: str):
            """Helper to send progress updates"""
            if progress_callback:
                await progress_callback(message)
        
        try:
            # CRITICAL: Ensure no existing screen session before starting
            # This prevents duplicate screen sessions for the same server
            # This check is essential for restart operations and edge cases
            screen_name = f"cs2server_{server.id}"
            check_cmd = f"screen -list | grep {screen_name} || true"
            check_success, check_output, _ = await self.execute_command(check_cmd)
            
            if check_success and check_output.strip() and screen_name in check_output:
                await send_progress(f"⚠ Existing screen session(s) detected for server {server.id}")
                await send_progress("Terminating all existing sessions to prevent duplicates...")
                
                # Terminate ALL screen sessions matching this pattern
                # Use pkill to ensure all processes are killed
                kill_all_cmd = f"screen -ls | grep {screen_name} | cut -d. -f1 | awk '{{print $1}}' | xargs -r -I {{}} screen -S {{}} -X quit; pkill -f 'SCREEN.*{screen_name}' || true"
                await self.execute_command(kill_all_cmd)
                
                # Wait and verify termination with retry logic
                for retry in range(3):
                    await asyncio.sleep(1)
                    verify_cmd = f"screen -list | grep {screen_name} || true"
                    verify_success, verify_output, _ = await self.execute_command(verify_cmd)
                    
                    if not verify_output.strip() or screen_name not in verify_output:
                        await send_progress("✓ All existing screen sessions terminated successfully")
                        break
                    
                    if retry < 2:
                        await send_progress(f"Retry {retry + 1}: Waiting for sessions to terminate...")
                        # Try killing again with more aggressive approach
                        await self.execute_command(kill_all_cmd)
                else:
                    # Final attempt with force kill of all related processes
                    await send_progress("⚠ Some screen sessions still exist, attempting final force termination...")
                    final_kill_cmd = f"pkill -9 -f 'SCREEN.*{screen_name}' || true; pkill -9 -f 'cs2server_{server.id}' || true"
                    await self.execute_command(final_kill_cmd)
                    await asyncio.sleep(1)
            
            # Kill any stray CS2 processes that might be running outside screen
            # This is an additional safety check to prevent duplicate processes
            await self._kill_stray_cs2_processes(server, progress_callback)
            
            # Perform universal self-check and auto-fix common issues
            selfcheck_success, selfcheck_msg = await self.perform_server_selfcheck(server, progress_callback)
            if not selfcheck_success:
                await send_progress(f"⚠ Warning: Self-check found issues: {selfcheck_msg}")
                await send_progress("Continuing with server start...")
            
            # Build start command with LGSM-style parameters
            cs2_executable = "./cs2"  # Use relative path when in correct directory
            
            # Get configuration with safe defaults
            default_map = server.default_map or "de_dust2"
            max_players = server.max_players or 32
            tickrate = server.tickrate or 128
            server_name = server.server_name or f"CS2 Server {server.id}"
            game_mode = server.game_mode or "competitive"
            game_type = server.game_type or "0"
            
            # Core parameters
            params = [
                "-dedicated",
                f"-port {server.game_port}",
                f"+map {default_map}",
                f"-maxplayers {max_players}",
                f"-tickrate {tickrate}",
                f'+hostname "{server_name}"',
            ]
            
            # Optional IP binding
            if server.ip_address:
                params.append(f"-ip {server.ip_address}")
            
            # Client port (usually game_port + 1)
            if server.client_port:
                params.append(f"+clientport {server.client_port}")
            elif server.game_port:
                params.append(f"+clientport {server.game_port + 1}")
            
            # Server password
            if server.server_password:
                params.append(f'+sv_password "{server.server_password}"')
            
            # RCON password
            if server.rcon_password:
                params.append(f'+rcon_password "{server.rcon_password}"')
            
            # Game mode and type
            params.append(f"+game_mode {game_mode}")
            params.append(f"+game_type {game_type}")
            
            # SourceTV configuration
            if server.tv_enable and server.tv_port:
                params.extend([
                    "+tv_enable 1",
                    f"+tv_port {server.tv_port}",
                    '+tv_name "GOTV"',
                ])
            
            # Additional custom parameters
            if server.additional_parameters:
                params.append(server.additional_parameters.strip())
            
            # Combine all parameters
            params_str = " ".join(params)
            
            # Get backend URL and API key for status reporting
            # Use server's backend_url if set, otherwise use global setting
            from modules.config import settings
            backend_url = server.backend_url or settings.BACKEND_URL
            api_key = server.api_key or ""
            
            # Check if autorestart script exists (should have been deployed during deployment)
            autorestart_script_path = f"{server.game_directory}/cs2_autorestart.sh"
            check_script_cmd = f"test -f {autorestart_script_path} && echo 'exists'"
            script_exists_success, script_exists_stdout, _ = await self.execute_command(check_script_cmd)
            
            # If script doesn't exist, deploy it now
            if not script_exists_success or 'exists' not in script_exists_stdout:
                await send_progress("Auto-restart script not found, deploying now...")
                
                # Read the autorestart script content
                import os
                script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                local_script_path = os.path.join(script_dir, "scripts", "cs2_autorestart.sh")
                
                try:
                    with open(local_script_path, 'r') as f:
                        script_content = f.read()
                    
                    # Create the script on remote server
                    create_script_cmd = f"cat > {autorestart_script_path} << 'EOFSCRIPT'\n{script_content}\nEOFSCRIPT"
                    success, stdout, stderr = await self.execute_command(create_script_cmd, timeout=10)
                    
                    if not success:
                        await send_progress(f"⚠ Warning: Could not deploy autorestart script: {stderr}")
                        await send_progress("Server will start without auto-restart protection")
                        use_autorestart = False
                    else:
                        # Make script executable
                        chmod_script_cmd = f"chmod +x {autorestart_script_path}"
                        await self.execute_command(chmod_script_cmd)
                        await send_progress("✓ Auto-restart wrapper script deployed")
                        use_autorestart = True
                except Exception as e:
                    await send_progress(f"⚠ Warning: Could not read autorestart script: {str(e)}")
                    await send_progress("Server will start without auto-restart protection")
                    use_autorestart = False
            else:
                await send_progress("✓ Auto-restart script found")
                use_autorestart = True
            
            # LGSM-style startup: Set working directory, library path, and redirect output
            # Working directory must be the bin directory for CS2 to find its libraries
            game_bin_dir = f"{server.game_directory}/cs2/game/bin/linuxsteamrt64"
            
            # Build the CS2 server start command (without screen wrapper)
            cs2_start_cmd = (
                f"cd {game_bin_dir} && "
                f"export LD_LIBRARY_PATH='{game_bin_dir}:${{LD_LIBRARY_PATH}}' && "
                f"{cs2_executable} {params_str}"
            )
            
            # Build the complete startup command with proper environment
            if use_autorestart and api_key:
                # Use autorestart wrapper with screen
                start_cmd = (
                    f"screen -dmS cs2server_{server.id} "
                    f"bash {autorestart_script_path} "
                    f"{server.id} '{api_key}' '{backend_url}' '{server.game_directory}' "
                    f"'{cs2_start_cmd}'"
                )
                await send_progress("✓ Starting with auto-restart protection enabled")
            else:
                # Fallback to simple screen start without autorestart
                start_cmd = (
                    f"cd {game_bin_dir} && "
                    f"export LD_LIBRARY_PATH=\"{game_bin_dir}:$LD_LIBRARY_PATH\" && "
                    f"screen -dmS cs2server_{server.id} "
                    f"bash -c '{cs2_executable} {params_str} 2>&1 | tee {server.game_directory}/cs2/game/csgo/console.log'"
                )
                if not api_key:
                    await send_progress("⚠ Warning: No API key configured, auto-restart reporting disabled")

            
            # Send startup information
            await send_progress("=" * 60)
            await send_progress("Starting CS2 Server...")
            await send_progress("=" * 60)
            await send_progress(f"Server ID: {server.id}")
            await send_progress(f"Port: {server.game_port}")
            await send_progress(f"Map: {default_map}")
            await send_progress(f"Max Players: {max_players}")
            await send_progress(f"Tickrate: {tickrate}")
            await send_progress(f"Game Mode: {game_mode}")
            await send_progress("=" * 60)
            
            success, stdout, stderr = await self.execute_command(start_cmd, timeout=10)
            
            if not success:
                await send_progress(f"Start command failed: {stderr}")
                return False, f"Start command failed: {stderr}"
            
            await send_progress("Server process started, streaming console output...")
            await send_progress("=" * 60)
            
            # Stream console output in real-time for first few seconds
            console_log_path = f"{server.game_directory}/cs2/game/csgo/console.log"
            
            # Wait a moment for log file to be created
            await asyncio.sleep(0.3)
            
            # Stream console output using tail -f with timeout
            # This will show the actual server startup messages (like srcds)
            stream_cmd = f"timeout 4 tail -f {console_log_path} 2>/dev/null || true"
            
            # Use execute_command_streaming to show real-time output
            try:
                await self.execute_command_streaming(stream_cmd, output_callback=progress_callback, timeout=5)
            except Exception as e:
                # Timeout is expected - just continue
                pass
            
            await send_progress("=" * 60)
            await send_progress("Initial startup output complete, verifying server status...")
            await send_progress("=" * 60)
            
            # Early check: Verify screen session was created
            # Wait a bit longer as initialization can take time
            await asyncio.sleep(0.8)
            screen_check = f"screen -list | grep cs2server_{server.id} || echo 'NO_SCREEN'"
            screen_success, screen_output, _ = await self.execute_command(screen_check)
            
            if 'NO_SCREEN' in screen_output:
                # Screen session never created or exited during initialization
                log_check = f"test -f {server.game_directory}/cs2/game/csgo/console.log && tail -150 {server.game_directory}/cs2/game/csgo/console.log || echo 'No log file'"
                _, immediate_log, _ = await self.execute_command(log_check, timeout=10)
                
                # Check if auto-restart is available
                can_restart, restart_msg = server_monitor.can_restart(server.id)
                
                # Check for specific errors in the log
                error_analysis = []
                auto_restart_possible = True
                
                if immediate_log and immediate_log != 'No log file':
                    if 'map' in immediate_log.lower() and 'load' in immediate_log.lower() and 'fail' in immediate_log.lower():
                        error_analysis.append("⚠ Map loading failed - the specified map may not exist or is corrupted")
                        auto_restart_possible = False  # Map issue won't be fixed by restart
                    if 'error' in immediate_log.lower():
                        error_analysis.append("⚠ Server reported errors during initialization")
                    if 'quit' in immediate_log.lower() or 'exit' in immediate_log.lower():
                        error_analysis.append("⚠ Server exited during startup")
                    if 'segmentation' in immediate_log.lower() or 'sigsegv' in immediate_log.lower():
                        error_analysis.append("⚠ Server crashed (segmentation fault)")
                    if 'bind' in immediate_log.lower() or 'address already in use' in immediate_log.lower():
                        error_analysis.append("⚠ Port binding failed - port may already be in use")
                        auto_restart_possible = False  # Port issue won't be fixed by restart
                    if 'steamclient.so' in immediate_log.lower() and 'fail' in immediate_log.lower():
                        error_analysis.append("⚠ CRITICAL: steamclient.so loading failed - may need to re-deploy server")
                        auto_restart_possible = False
                
                # Attempt auto-restart if applicable
                if can_restart and auto_restart_possible and progress_callback:
                    await send_progress("\n" + "=" * 60)
                    await send_progress("AUTO-RESTART: Server crashed, attempting automatic restart...")
                    await send_progress(f"Restart status: {restart_msg}")
                    await send_progress("=" * 60)
                    
                    server_monitor.record_restart(server.id)
                    
                    # Wait a bit before restart
                    await asyncio.sleep(2)
                    
                    # Retry starting the server (recursive call with same callback)
                    return await self.start_server(server, progress_callback)
                
                # If auto-restart not available or not applicable, return error
                error_msg = "Server failed to start - process exited during initialization.\n\n"
                if error_analysis:
                    error_msg += "Detected Issues:\n" + "\n".join(error_analysis) + "\n\n"
                if not can_restart:
                    error_msg += f"Auto-restart: {restart_msg}\n\n"
                elif not auto_restart_possible:
                    error_msg += "Auto-restart: Disabled due to configuration issues detected\n\n"
                error_msg += f"Console output (last 150 lines):\n{immediate_log[:3000]}"
                return False, error_msg
            
            # Wait 1 second and check if server is still alive (detect immediate crashes)
            await asyncio.sleep(1)
            quick_check = f"screen -list | grep cs2server_{server.id} || echo 'CRASHED'"
            _, quick_output, _ = await self.execute_command(quick_check)
            
            if 'CRASHED' in quick_output:
                # Server crashed within 1 second - get logs immediately
                log_check = f"test -f {server.game_directory}/cs2/game/csgo/console.log && tail -100 {server.game_directory}/cs2/game/csgo/console.log || echo 'No log file'"
                _, crash_log, _ = await self.execute_command(log_check, timeout=10)
                
                # Check for core dumps
                core_check = f"ls -lt {server.game_directory}/cs2/game/bin/linuxsteamrt64/core* 2>/dev/null | head -1 || echo 'No core dump'"
                _, core_output, _ = await self.execute_command(core_check)
                
                crash_info = f"Server crashed within 1 second of starting.\n\n"
                crash_info += f"=== Console Log (last 100 lines) ===\n{crash_log[:3000]}\n\n"
                if 'No core dump' not in core_output:
                    crash_info += f"=== Core Dump Found ===\n{core_output}\n"
                return False, crash_info
            
            # Wait additional time for server to fully initialize (CS2 can take time)
            await asyncio.sleep(3)
            
            # Check if server is running - try multiple methods
            # Method 1: Check screen session
            check_cmd = f"screen -list | grep cs2server_{server.id} || true"
            success, stdout, stderr = await self.execute_command(check_cmd)
            
            if stdout and f"cs2server_{server.id}" in stdout:
                return True, "Server started successfully"
            
            # Method 2: Check if CS2 process is running
            process_check = f"pgrep -f 'cs2.*-port {server.game_port}' && echo 'running' || echo 'not running'"
            proc_success, proc_stdout, _ = await self.execute_command(process_check)
            
            if 'running' in proc_stdout:
                return True, "Server started successfully (process verified)"
            
            # Method 3: Check if port is listening
            port_check = f"netstat -tuln | grep ':{server.game_port} ' || ss -tuln | grep ':{server.game_port} ' || echo 'not listening'"
            port_success, port_stdout, _ = await self.execute_command(port_check)
            
            if 'not listening' not in port_stdout and port_stdout.strip():
                return True, "Server started successfully (port listening)"
            
            # If no check confirms the server is running, it likely failed to start
            # Gather comprehensive diagnostic information
            diagnostics = []
            
            # Check console log with more lines
            log_check = f"test -f {server.game_directory}/cs2/game/csgo/console.log && tail -100 {server.game_directory}/cs2/game/csgo/console.log || echo 'No log file found'"
            log_success, log_output, _ = await self.execute_command(log_check, timeout=10)
            
            # Check for core dumps (indicates crash)
            core_check = f"ls -lt {server.game_directory}/cs2/game/bin/linuxsteamrt64/core* 2>/dev/null | head -1 || echo 'No core dump'"
            _, core_output, _ = await self.execute_command(core_check)
            
            # Check for common errors in the log
            error_indicators = []
            if log_output and log_output != 'No log file found':
                if 'bind:' in log_output.lower() or 'address already in use' in log_output.lower():
                    error_indicators.append("Port binding issue - port may be in use")
                if 'permission denied' in log_output.lower():
                    error_indicators.append("Permission denied - check file permissions")
                if 'map' in log_output.lower() and ('not found' in log_output.lower() or 'failed' in log_output.lower()):
                    error_indicators.append("Map loading failed - check if map exists")
                if 'library' in log_output.lower() or '.so' in log_output.lower():
                    error_indicators.append("Missing library dependency")
                if 'segmentation fault' in log_output.lower() or 'sigsegv' in log_output.lower() or 'core dumped' in log_output.lower():
                    error_indicators.append("Segmentation fault - server crashed")
                if 'failed to load' in log_output.lower():
                    error_indicators.append("Failed to load required resources")
                if 'error' in log_output.lower():
                    # Count how many errors
                    error_count = log_output.lower().count('error')
                    if error_count > 0:
                        error_indicators.append(f"Found {error_count} error(s) in console log")
            
            diagnostics.append("=== Startup Diagnostics ===")
            diagnostics.append(f"Screen session: {'NOT FOUND' if not stdout or f'cs2server_{server.id}' not in stdout else 'Found but process may have exited'}")
            diagnostics.append(f"Process running: {'NO' if 'not running' in proc_stdout else 'UNKNOWN'}")
            diagnostics.append(f"Port {server.game_port} listening: {'NO' if 'not listening' in port_stdout or not port_stdout.strip() else 'UNKNOWN'}")
            
            if 'No core dump' not in core_output:
                diagnostics.append(f"Core dump: FOUND - {core_output.strip()[:200]}")
            
            if error_indicators:
                diagnostics.append("\n=== Detected Issues ===")
                for indicator in error_indicators:
                    diagnostics.append(f"⚠ {indicator}")
            
            # Check working directory and binary
            binary_check = f"test -f {server.game_directory}/cs2/game/bin/linuxsteamrt64/cs2 && echo 'exists' || echo 'missing'"
            binary_success, binary_stdout, _ = await self.execute_command(binary_check)
            if 'missing' in binary_stdout:
                diagnostics.append("\n⚠ CS2 executable not found - deployment may have failed")
            
            # Check library dependencies
            lib_check = f"cd {server.game_directory}/cs2/game/bin/linuxsteamrt64 && ldd ./cs2 2>&1 | grep 'not found' || echo 'all libraries found'"
            lib_success, lib_stdout, _ = await self.execute_command(lib_check, timeout=10)
            if 'not found' in lib_stdout:
                diagnostics.append(f"\n=== Missing Libraries ===")
                diagnostics.append(lib_stdout.strip())
            
            # Check if steamclient.so exists (required)
            steamclient_check = f"test -f {server.game_directory}/cs2/game/bin/linuxsteamrt64/steamclient.so && echo 'found' || echo 'MISSING steamclient.so'"
            _, steamclient_output, _ = await self.execute_command(steamclient_check)
            if 'MISSING' in steamclient_output:
                diagnostics.append("\n⚠ CRITICAL: steamclient.so not found - SteamCMD installation may be incomplete")
            
            diagnostics.append("\n=== Console Log (last 100 lines) ===")
            diagnostics.append(log_output[:3000] if log_output else 'No log output available')
            
            # Add troubleshooting suggestions
            diagnostics.append("\n=== Troubleshooting Suggestions ===")
            if error_indicators:
                diagnostics.append("1. Check the detected issues above")
            diagnostics.append("2. Verify all files were installed: Check deployment logs")
            diagnostics.append("3. Ensure ports are available: netstat -tuln | grep " + str(server.game_port))
            diagnostics.append("4. Check server permissions: ls -la " + server.game_directory + "/cs2/game/bin/linuxsteamrt64/cs2")
            
            diagnostic_message = '\n'.join(diagnostics)
            
            return False, f"Server failed to start after multiple checks.\n\n{diagnostic_message}"
        
        except Exception as e:
            return False, f"Start error: {str(e)}"
        finally:
            await self.disconnect()
    
    async def stop_server(self, server: Server) -> Tuple[bool, str]:
        """Stop CS2 server with retry logic to ensure complete termination"""
        success, msg = await self.connect(server)
        if not success:
            return False, f"Connection failed: {msg}"
        
        try:
            screen_name = f"cs2server_{server.id}"
            
            # Check if screen session exists
            check_cmd = f"screen -list | grep {screen_name} || true"
            check_success, check_output, _ = await self.execute_command(check_cmd)
            
            if not check_output.strip() or screen_name not in check_output:
                return True, "Server is not running (no screen session found)"
            
            # Stop screen session
            stop_cmd = f"screen -S {screen_name} -X quit"
            await self.execute_command(stop_cmd)
            
            # Verify termination with retry logic
            for retry in range(5):
                await asyncio.sleep(1)
                
                verify_cmd = f"screen -list | grep {screen_name} || true"
                verify_success, verify_output, _ = await self.execute_command(verify_cmd)
                
                if not verify_output.strip() or screen_name not in verify_output:
                    return True, "Server stopped successfully"
                
                if retry < 4:
                    # Retry sending quit command
                    await self.execute_command(stop_cmd)
            
            # Final attempt with force kill if still running
            kill_cmd = f"pkill -f 'SCREEN.*{screen_name}' || true"
            await self.execute_command(kill_cmd)
            await asyncio.sleep(1)
            
            # Final verification
            final_check_cmd = f"screen -list | grep {screen_name} || true"
            final_success, final_output, _ = await self.execute_command(final_check_cmd)
            
            if not final_output.strip() or screen_name not in final_output:
                return True, "Server stopped successfully (force terminated)"
            else:
                return False, "Server failed to stop after multiple attempts"
        
        except Exception as e:
            return False, f"Stop error: {str(e)}"
        finally:
            await self.disconnect()
    
    async def _send_progress_if_callback(self, progress_callback, message: str):
        """
        Shared helper to send progress updates if callback is provided
        
        Args:
            progress_callback: Optional callback for progress messages
            message: Progress message to send
        """
        if progress_callback:
            if asyncio.iscoroutinefunction(progress_callback):
                await progress_callback(message)
            else:
                progress_callback(message)
    
    async def _kill_steamcmd_processes(self, server: Server, progress_callback=None) -> None:
        """
        Kill any existing steamcmd processes for this server to prevent concurrent updates
        
        Args:
            server: Server instance
            progress_callback: Optional callback for progress messages
        """
        try:
            # Find steamcmd processes related to this server's directory
            # We look for processes that contain both "steamcmd" and the server's game directory path
            game_dir = server.game_directory
            
            # First, check if there are any steamcmd processes running for this server
            check_cmd = f"pgrep -f 'steamcmd.*{game_dir}' || true"
            success, stdout, stderr = await self.execute_command(check_cmd, timeout=10)
            
            if stdout.strip():
                pids = stdout.strip().split('\n')
                await self._send_progress_if_callback(progress_callback, f"⚠ Found {len(pids)} existing steamcmd process(es), terminating...")
                
                # Kill the processes
                for pid in pids:
                    if pid:
                        kill_cmd = f"kill -9 {pid} 2>/dev/null || true"
                        await self.execute_command(kill_cmd, timeout=5)
                
                # Give a moment for processes to terminate
                await asyncio.sleep(0.5)
                
                # Verify they're gone
                verify_cmd = f"pgrep -f 'steamcmd.*{game_dir}' || true"
                success, verify_output, _ = await self.execute_command(verify_cmd, timeout=10)
                
                if verify_output.strip():
                    await self._send_progress_if_callback(progress_callback, "⚠ Some steamcmd processes may still be running")
                else:
                    await self._send_progress_if_callback(progress_callback, "✓ All existing steamcmd processes terminated")
            
        except Exception as e:
            # Non-critical error, log but continue
            await self._send_progress_if_callback(progress_callback, f"Note: Error checking for existing steamcmd processes: {str(e)}")
    
    async def _kill_stray_cs2_processes(self, server: Server, progress_callback=None) -> None:
        """
        Kill any CS2 server processes running outside of screen sessions
        
        This prevents duplicate processes when starting/updating/validating servers.
        Only kills CS2 processes matching this server's port to avoid affecting other servers.
        Uses word boundary matching to ensure exact port matching (e.g., port 27015 won't match 270).
        
        Args:
            server: Server instance
            progress_callback: Optional callback for progress messages
        """
        try:
            # Find CS2 processes for this server's port with exact matching
            # Use word boundary \b to prevent matching ports as substrings (e.g., 270 matching in 27015)
            # The pattern 'cs2.*-port\s+{port}\b' ensures we match "-port 27015" but not "-port 270159"
            check_cmd = f"pgrep -f 'cs2.*-port\\s+{server.game_port}\\b' || true"
            success, stdout, stderr = await self.execute_command(check_cmd, timeout=10)
            
            if stdout.strip():
                pids = stdout.strip().split('\n')
                await self._send_progress_if_callback(progress_callback, f"⚠ Found {len(pids)} stray CS2 process(es) on port {server.game_port}, terminating...")
                
                # Kill the processes
                for pid in pids:
                    if pid:
                        kill_cmd = f"kill -9 {pid} 2>/dev/null || true"
                        await self.execute_command(kill_cmd, timeout=5)
                
                # Give a moment for processes to terminate
                await asyncio.sleep(0.5)
                
                # Verify they're gone using the same precise pattern
                verify_cmd = f"pgrep -f 'cs2.*-port\\s+{server.game_port}\\b' || true"
                success, verify_output, _ = await self.execute_command(verify_cmd, timeout=10)
                
                if verify_output.strip():
                    await self._send_progress_if_callback(progress_callback, "⚠ Some CS2 processes may still be running")
                else:
                    await self._send_progress_if_callback(progress_callback, "✓ All stray CS2 processes terminated")
            
        except Exception as e:
            # Non-critical error, log but continue
            await self._send_progress_if_callback(progress_callback, f"Note: Error checking for stray CS2 processes: {str(e)}")
    
    async def update_server(self, server: Server, progress_callback=None) -> Tuple[bool, str]:
        """Update CS2 server using SteamCMD (without validation)"""
        success, msg = await self.connect(server)
        if not success:
            return False, f"Connection failed: {msg}"
        
        async def send_progress(message: str):
            """Helper to send progress updates"""
            if progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
                    await progress_callback(message)
                else:
                    progress_callback(message)
        
        try:
            await send_progress("Starting server update...")
            
            # Kill any existing steamcmd processes for this server
            await self._kill_steamcmd_processes(server, progress_callback)
            
            # Check if server is running and stop it first
            screen_name = f"cs2server_{server.id}"
            check_cmd = f"screen -list | grep {screen_name} || true"
            check_success, check_output, _ = await self.execute_command(check_cmd)
            
            was_running = check_output.strip() and screen_name in check_output
            if was_running:
                await send_progress("Server is running, stopping before update...")
                
                # Use improved stop logic
                stop_cmd = f"screen -S {screen_name} -X quit"
                await self.execute_command(stop_cmd)
                
                # Wait and verify with retry
                for retry in range(3):
                    await asyncio.sleep(1)
                    verify_cmd = f"screen -list | grep {screen_name} || true"
                    verify_success, verify_output, _ = await self.execute_command(verify_cmd)
                    
                    if not verify_output.strip() or screen_name not in verify_output:
                        await send_progress("✓ Server stopped successfully")
                        break
                    
                    if retry < 2:
                        await self.execute_command(stop_cmd)
                else:
                    await send_progress("⚠ Force stopping server...")
                    kill_cmd = f"pkill -f 'SCREEN.*{screen_name}' || true"
                    await self.execute_command(kill_cmd)
                    await asyncio.sleep(1)
            
            # Kill any stray CS2 processes that might be running outside screen
            await self._kill_stray_cs2_processes(server, progress_callback)
            
            # Navigate to game directory
            game_dir = server.game_directory
            steamcmd_dir = f"{game_dir}/steamcmd"
            
            # Run SteamCMD update command (without validate)
            update_cmd = (
                f"cd {steamcmd_dir} && "
                f"./steamcmd.sh "
                f"+force_install_dir {game_dir}/cs2 "
                f"+login anonymous "
                f"+app_update 730 "
                f"+quit"
            )
            
            # Display command preview before execution
            await send_progress("=" * 60)
            await send_progress("即将执行的命令 / Commands to be executed:")
            await send_progress("=" * 60)
            await send_progress(f"📝 SteamCMD Update Command:")
            await send_progress(f"   {update_cmd}")
            await send_progress("=" * 60)
            await send_progress("Updating CS2 server files via SteamCMD...")
            
            success, stdout, stderr = await self.execute_command_streaming(
                update_cmd,
                output_callback=send_progress,
                timeout=1800  # 30 minutes timeout for updates
            )
            
            if not success and stderr and "error" in stderr.lower():
                await send_progress(f"Update completed with warnings: {stderr}")
            else:
                await send_progress("CS2 server updated successfully")
            
            # Restart server if it was running before
            if was_running:
                await send_progress("Restarting server...")
                # We'll let the caller handle the restart to keep this method focused
                await send_progress("Server update complete. Please restart the server.")
            
            return True, "Server updated successfully"
        
        except Exception as e:
            await send_progress(f"Update error: {str(e)}")
            return False, f"Update error: {str(e)}"
        finally:
            await self.disconnect()
    
    async def validate_server(self, server: Server, progress_callback=None) -> Tuple[bool, str]:
        """Update and validate CS2 server files using SteamCMD"""
        success, msg = await self.connect(server)
        if not success:
            return False, f"Connection failed: {msg}"
        
        async def send_progress(message: str):
            """Helper to send progress updates"""
            if progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
                    await progress_callback(message)
                else:
                    progress_callback(message)
        
        try:
            await send_progress("Starting server update and validation...")
            
            # Kill any existing steamcmd processes for this server
            await self._kill_steamcmd_processes(server, progress_callback)
            
            # Check if server is running and stop it first
            screen_name = f"cs2server_{server.id}"
            check_cmd = f"screen -list | grep {screen_name} || true"
            check_success, check_output, _ = await self.execute_command(check_cmd)
            
            was_running = check_output.strip() and screen_name in check_output
            if was_running:
                await send_progress("Server is running, stopping before update...")
                
                # Use improved stop logic
                stop_cmd = f"screen -S {screen_name} -X quit"
                await self.execute_command(stop_cmd)
                
                # Wait and verify with retry
                for retry in range(3):
                    await asyncio.sleep(1)
                    verify_cmd = f"screen -list | grep {screen_name} || true"
                    verify_success, verify_output, _ = await self.execute_command(verify_cmd)
                    
                    if not verify_output.strip() or screen_name not in verify_output:
                        await send_progress("✓ Server stopped successfully")
                        break
                    
                    if retry < 2:
                        await self.execute_command(stop_cmd)
                else:
                    await send_progress("⚠ Force stopping server...")
                    kill_cmd = f"pkill -f 'SCREEN.*{screen_name}' || true"
                    await self.execute_command(kill_cmd)
                    await asyncio.sleep(1)
            
            # Kill any stray CS2 processes that might be running outside screen
            await self._kill_stray_cs2_processes(server, progress_callback)
            
            # Navigate to game directory
            game_dir = server.game_directory
            steamcmd_dir = f"{game_dir}/steamcmd"
            
            # Run SteamCMD update command with validation
            update_cmd = (
                f"cd {steamcmd_dir} && "
                f"./steamcmd.sh "
                f"+force_install_dir {game_dir}/cs2 "
                f"+login anonymous "
                f"+app_update 730 validate "
                f"+quit"
            )
            
            # Display command preview before execution
            await send_progress("=" * 60)
            await send_progress("即将执行的命令 / Commands to be executed:")
            await send_progress("=" * 60)
            await send_progress(f"📝 SteamCMD Update + Validate Command:")
            await send_progress(f"   {update_cmd}")
            await send_progress("=" * 60)
            await send_progress("Updating and validating CS2 server files via SteamCMD...")
            await send_progress("This may take a while as all files will be validated...")
            
            success, stdout, stderr = await self.execute_command_streaming(
                update_cmd,
                output_callback=send_progress,
                timeout=3600  # 60 minutes timeout for validation
            )
            
            if not success and stderr and "error" in stderr.lower():
                await send_progress(f"Validation completed with warnings: {stderr}")
            else:
                await send_progress("CS2 server updated and validated successfully")
            
            # Restart server if it was running before
            if was_running:
                await send_progress("Restarting server...")
                await send_progress("Server validation complete. Please restart the server.")
            
            return True, "Server updated and validated successfully"
        
        except Exception as e:
            await send_progress(f"Validation error: {str(e)}")
            return False, f"Validation error: {str(e)}"
        finally:
            await self.disconnect()
    
    async def get_server_status(self, server: Server) -> Tuple[bool, str]:
        """Get server status"""
        success, msg = await self.connect(server)
        if not success:
            return False, "offline"
        
        try:
            check_cmd = f"screen -list | grep cs2server_{server.id}"
            success, stdout, stderr = await self.execute_command(check_cmd)
            
            if success and stdout:
                return True, "running"
            else:
                return True, "stopped"
        
        except Exception as e:
            return False, "unknown"
        finally:
            await self.disconnect()
    
    async def install_metamod(self, server: Server, progress_callback=None) -> Tuple[bool, str]:
        """
        Install Metamod:Source 2.0 for CS2 server
        
        Args:
            server: Server instance
            progress_callback: Optional async callback for progress updates
        Returns: (success: bool, message: str)
        """
        async def send_progress(message: str):
            """Helper to send progress updates"""
            if progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
                    await progress_callback(message)
                else:
                    progress_callback(message)
        
        success, msg = await self.connect(server)
        if not success:
            return False, f"Connection failed: {msg}"
        
        try:
            await send_progress("=" * 60)
            await send_progress("Installing Metamod:Source 2.0 for CS2...")
            await send_progress("=" * 60)
            
            # Check if CS2 is installed
            cs2_dir = f"{server.game_directory}/cs2"
            check_cmd = f"test -d {cs2_dir} && echo 'exists'"
            check_success, check_stdout, _ = await self.execute_command(check_cmd)
            
            if not check_success or 'exists' not in check_stdout:
                return False, "CS2 server not found. Please deploy the server first."
            
            await send_progress("✓ CS2 server directory found")
            
            # Get latest Metamod version from the web
            await send_progress("Fetching latest Metamod:Source version...")
            
            # Scrape the latest version from sourcemm.net downloads page
            # The page lists dev builds at https://www.sourcemm.net/downloads.php?branch=master
            get_latest_cmd = (
                "curl -sL 'https://www.sourcemm.net/downloads.php?branch=master' | "
                "grep -o 'https://mms.alliedmods.net/mmsdrop/2.0/mmsource-2.0.0-git[0-9]*-linux.tar.gz' | "
                "head -1"
            )
            success, metamod_url, stderr = await self.execute_command(get_latest_cmd, timeout=30)
            
            if not success or not metamod_url.strip():
                # Fallback to a known recent version
                await send_progress("⚠ Could not fetch latest version, using fallback URL...")
                metamod_url = "https://mms.alliedmods.net/mmsdrop/2.0/mmsource-2.0.0-git1374-linux.tar.gz"
            else:
                metamod_url = metamod_url.strip()
                await send_progress(f"✓ Found latest version: {metamod_url}")
            
            # Create temp directory for download
            temp_dir = f"/tmp/metamod_install_{server.id}"
            await send_progress(f"Creating temporary directory: {temp_dir}")
            await self.execute_command(f"mkdir -p {temp_dir}")
            
            # Download Metamod
            await send_progress(f"Downloading Metamod from {metamod_url}...")
            # Use curl as fallback if wget doesn't work well, with better error handling
            download_cmd = f"curl -L -o {temp_dir}/metamod.tar.gz {metamod_url} || wget --no-check-certificate -O {temp_dir}/metamod.tar.gz {metamod_url}"
            success, stdout, stderr = await self.execute_command_streaming(
                download_cmd,
                output_callback=send_progress,
                timeout=180
            )
            
            # Always verify the file was downloaded regardless of exit code
            check_cmd = f"test -f {temp_dir}/metamod.tar.gz && echo 'exists'"
            check_success, check_stdout, _ = await self.execute_command(check_cmd)
            
            if not check_success or 'exists' not in check_stdout:
                await self.execute_command(f"rm -rf {temp_dir}")
                error_detail = f"Download failed. stderr: {stderr[:500] if stderr else 'No error output'}"
                return False, f"Metamod download failed: {error_detail}"
            
            # Check file size to ensure it's not empty
            size_cmd = f"stat -f%z {temp_dir}/metamod.tar.gz 2>/dev/null || stat -c%s {temp_dir}/metamod.tar.gz 2>/dev/null"
            size_success, size_out, _ = await self.execute_command(size_cmd)
            if size_success and size_out.strip():
                file_size = int(size_out.strip())
                if file_size < 1000:  # Less than 1KB is probably an error
                    await self.execute_command(f"rm -rf {temp_dir}")
                    return False, f"Downloaded file is too small ({file_size} bytes). Download may have failed."
                await send_progress(f"✓ Downloaded {file_size} bytes")
            
            await send_progress("✓ Metamod downloaded successfully")
            
            # Extract Metamod to CS2 csgo directory (tar contains addons/metamod structure)
            csgo_dir = f"{cs2_dir}/game/csgo"
            await send_progress(f"Extracting Metamod to {csgo_dir}...")
            extract_cmd = f"tar -xzf {temp_dir}/metamod.tar.gz -C {csgo_dir}"
            success, stdout, stderr = await self.execute_command(extract_cmd, timeout=60)
            
            if not success:
                await self.execute_command(f"rm -rf {temp_dir}")
                return False, f"Metamod extraction failed: {stderr}"
            
            await send_progress("✓ Metamod extracted successfully")
            
            # Modify gameinfo.gi to add Metamod
            gameinfo_path = f"{cs2_dir}/game/csgo/gameinfo.gi"
            await send_progress("Updating gameinfo.gi...")
            
            # Check if gameinfo.gi exists
            check_cmd = f"test -f {gameinfo_path} && echo 'exists'"
            check_success, check_stdout, _ = await self.execute_command(check_cmd)
            
            if not check_success or 'exists' not in check_stdout:
                await self.execute_command(f"rm -rf {temp_dir}")
                return False, "gameinfo.gi not found. Server may not be properly installed."
            
            # Check if Metamod is already in gameinfo.gi
            check_mm_cmd = f"grep -q 'addons/metamod' {gameinfo_path} && echo 'found' || echo 'notfound'"
            check_success, check_stdout, _ = await self.execute_command(check_mm_cmd)
            
            if 'found' in check_stdout:
                await send_progress("✓ Metamod already configured in gameinfo.gi")
            else:
                # Backup gameinfo.gi
                backup_cmd = f"cp {gameinfo_path} {gameinfo_path}.backup"
                await self.execute_command(backup_cmd)
                await send_progress("✓ Created backup of gameinfo.gi")
                
                # Add Metamod to gameinfo.gi
                # We need to add "Game csgo/addons/metamod" after the Game_LowViolence line
                sed_cmd = (
                    f"sed -i '/Game_LowViolence/a\\			Game\\tcsgo/addons/metamod' {gameinfo_path}"
                )
                success, stdout, stderr = await self.execute_command(sed_cmd)
                
                if not success:
                    await send_progress("⚠ Warning: Could not automatically update gameinfo.gi")
                    await send_progress("You may need to manually add 'Game csgo/addons/metamod' to gameinfo.gi")
                else:
                    await send_progress("✓ gameinfo.gi updated successfully")
            
            # Clean up temp directory
            await self.execute_command(f"rm -rf {temp_dir}")
            
            # Verify installation
            metamod_dir = f"{cs2_dir}/game/csgo/addons/metamod"
            verify_cmd = f"test -d {metamod_dir} && echo 'installed'"
            verify_success, verify_stdout, _ = await self.execute_command(verify_cmd)
            
            if verify_success and 'installed' in verify_stdout:
                await send_progress("=" * 60)
                await send_progress("✓ Metamod:Source installed successfully!")
                await send_progress("=" * 60)
                await send_progress("NOTE: You may need to restart your server for changes to take effect.")
                await send_progress("After server updates, you may need to re-add the Metamod line to gameinfo.gi")
                return True, "Metamod:Source installed successfully"
            else:
                return False, "Metamod installation verification failed"
        
        except Exception as e:
            await send_progress(f"Installation error: {str(e)}")
            return False, f"Installation error: {str(e)}"
        finally:
            await self.disconnect()
    
    async def install_counterstrikesharp(self, server: Server, progress_callback=None) -> Tuple[bool, str]:
        """
        Install CounterStrikeSharp for CS2 server
        
        Args:
            server: Server instance
            progress_callback: Optional async callback for progress updates
        Returns: (success: bool, message: str)
        """
        async def send_progress(message: str):
            """Helper to send progress updates"""
            if progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
                    await progress_callback(message)
                else:
                    progress_callback(message)
        
        success, msg = await self.connect(server)
        if not success:
            return False, f"Connection failed: {msg}"
        
        try:
            await send_progress("=" * 60)
            await send_progress("Installing CounterStrikeSharp for CS2...")
            await send_progress("=" * 60)
            
            # Check if CS2 is installed
            cs2_dir = f"{server.game_directory}/cs2"
            check_cmd = f"test -d {cs2_dir} && echo 'exists'"
            check_success, check_stdout, _ = await self.execute_command(check_cmd)
            
            if not check_success or 'exists' not in check_stdout:
                return False, "CS2 server not found. Please deploy the server first."
            
            await send_progress("✓ CS2 server directory found")
            
            # Check if Metamod is installed (required for CounterStrikeSharp)
            metamod_dir = f"{cs2_dir}/game/csgo/addons/metamod"
            check_mm_cmd = f"test -d {metamod_dir} && echo 'exists'"
            check_mm_success, check_mm_stdout, _ = await self.execute_command(check_mm_cmd)
            
            if not check_mm_success or 'exists' not in check_mm_stdout:
                await send_progress("⚠ Warning: Metamod not found. Installing Metamod first...")
                mm_success, mm_msg = await self.install_metamod(server, progress_callback)
                if not mm_success:
                    return False, f"Metamod installation failed: {mm_msg}"
            else:
                await send_progress("✓ Metamod already installed")
            
            # Get latest CounterStrikeSharp release from GitHub
            await send_progress("Fetching latest CounterStrikeSharp release from GitHub...")
            
            # Use GitHub API to get the latest release - specifically look for with-runtime-linux
            api_cmd = (
                "curl -s https://api.github.com/repos/roflmuffin/CounterStrikeSharp/releases/latest | "
                "grep -oP '\"browser_download_url\": \"\\K[^\"]*counterstrikesharp-with-runtime-linux[^\"]*\\.zip' | head -1"
            )
            success, css_url, stderr = await self.execute_command(api_cmd, timeout=30)
            
            if not success or not css_url.strip():
                # Fallback: try to get any linux zip and filter
                await send_progress("⚠ Trying alternative API query...")
                alt_cmd = (
                    "curl -s https://api.github.com/repos/roflmuffin/CounterStrikeSharp/releases/latest | "
                    "grep '\"browser_download_url\"' | grep 'with-runtime-linux' | grep -oP 'https://[^\"]*\\.zip' | head -1"
                )
                success, css_url, _ = await self.execute_command(alt_cmd, timeout=30)
                
                if not success or not css_url.strip():
                    # Last fallback - construct URL from version tag
                    await send_progress("⚠ Could not fetch from GitHub API, constructing fallback URL...")
                    # Get the latest tag version
                    tag_cmd = "curl -s https://api.github.com/repos/roflmuffin/CounterStrikeSharp/releases/latest | grep '\"tag_name\"' | grep -oP 'v[0-9.]+' | head -1"
                    tag_success, tag, _ = await self.execute_command(tag_cmd, timeout=30)
                    
                    if tag_success and tag.strip():
                        version = tag.strip().lstrip('v')
                        css_url = f"https://github.com/roflmuffin/CounterStrikeSharp/releases/download/{tag.strip()}/counterstrikesharp-with-runtime-linux-{version}.zip"
                        await send_progress(f"Using constructed URL for version {version}")
                    else:
                        return False, "Could not determine CounterStrikeSharp version from GitHub API"
            else:
                css_url = css_url.strip()
            
            await send_progress(f"Download URL: {css_url}")
            
            # Create temp directory for download
            temp_dir = f"/tmp/css_install_{server.id}"
            await send_progress(f"Creating temporary directory: {temp_dir}")
            await self.execute_command(f"mkdir -p {temp_dir}")
            
            # Download CounterStrikeSharp
            await send_progress("Downloading CounterStrikeSharp...")
            # Use curl as fallback if wget doesn't work well
            download_cmd = f"curl -L -o {temp_dir}/counterstrikesharp.zip {css_url} || wget --no-check-certificate -O {temp_dir}/counterstrikesharp.zip {css_url}"
            success, stdout, stderr = await self.execute_command_streaming(
                download_cmd,
                output_callback=send_progress,
                timeout=300  # 5 minutes for larger download
            )
            
            # Always verify the file was downloaded
            check_cmd = f"test -f {temp_dir}/counterstrikesharp.zip && echo 'exists'"
            check_success, check_stdout, _ = await self.execute_command(check_cmd)
            
            if not check_success or 'exists' not in check_stdout:
                await self.execute_command(f"rm -rf {temp_dir}")
                error_detail = f"Download failed. stderr: {stderr[:500] if stderr else 'No error output'}"
                return False, f"CounterStrikeSharp download failed: {error_detail}"
            
            # Check file size
            size_cmd = f"stat -f%z {temp_dir}/counterstrikesharp.zip 2>/dev/null || stat -c%s {temp_dir}/counterstrikesharp.zip 2>/dev/null"
            size_success, size_out, _ = await self.execute_command(size_cmd)
            if size_success and size_out.strip():
                file_size = int(size_out.strip())
                if file_size < 10000:  # Less than 10KB is probably an error
                    await self.execute_command(f"rm -rf {temp_dir}")
                    return False, f"Downloaded file is too small ({file_size} bytes). Download may have failed."
                await send_progress(f"✓ Downloaded {file_size} bytes")
            
            await send_progress("✓ CounterStrikeSharp downloaded successfully")
            
            # Check if unzip is available and try to install if missing
            check_unzip = "command -v unzip"
            unzip_success, _, _ = await self.execute_command(check_unzip)
            
            if not unzip_success:
                await send_progress("⚠ Warning: unzip not found. Attempting to install...")
                
                # Check package manager
                check_apt = "command -v apt-get > /dev/null && echo 'apt' || echo 'none'"
                _, pkg_mgr, _ = await self.execute_command(check_apt)
                
                if 'apt' in pkg_mgr:
                    # Try to install without sudo first
                    install_cmd = "apt-get update && apt-get install -y unzip"
                    success, stdout, stderr = await self.execute_command(install_cmd, timeout=120)
                    
                    if not success:
                        # Try with sudo if available
                        if server.sudo_password:
                            await send_progress("Trying to install unzip with sudo...")
                            install_cmd = f"echo '{server.sudo_password}' | sudo -S apt-get update && echo '{server.sudo_password}' | sudo -S apt-get install -y unzip"
                            success, stdout, stderr = await self.execute_command(install_cmd, timeout=120)
                            
                            if success:
                                await send_progress("✓ unzip installed successfully")
                            else:
                                await self.execute_command(f"rm -rf {temp_dir}")
                                return False, f"Could not install unzip. Please run: sudo apt-get install unzip\nError: {stderr[:200]}"
                        else:
                            await self.execute_command(f"rm -rf {temp_dir}")
                            return False, "unzip not found and no sudo password provided. Please install unzip: sudo apt-get install unzip"
                    else:
                        await send_progress("✓ unzip installed successfully")
                    
                    # Verify unzip is now available
                    unzip_success, _, _ = await self.execute_command(check_unzip)
                    if not unzip_success:
                        await self.execute_command(f"rm -rf {temp_dir}")
                        return False, "unzip installation completed but command still not found. Please check system PATH."
                else:
                    await self.execute_command(f"rm -rf {temp_dir}")
                    return False, "unzip not found and package manager not detected. Please install unzip manually."
            else:
                await send_progress("✓ unzip is available")
            
            # Extract CounterStrikeSharp to CS2 directory
            # The zip contains an 'addons' folder that should merge with the existing addons
            await send_progress("Extracting CounterStrikeSharp...")
            extract_cmd = f"unzip -o {temp_dir}/counterstrikesharp.zip -d {cs2_dir}/game/csgo/"
            success, stdout, stderr = await self.execute_command(extract_cmd, timeout=120)
            
            # Check if extraction actually succeeded by checking the directory
            verify_extract = f"test -d {cs2_dir}/game/csgo/addons/counterstrikesharp && echo 'extracted'"
            verify_success, verify_out, _ = await self.execute_command(verify_extract)
            
            if not verify_success or 'extracted' not in verify_out:
                await self.execute_command(f"rm -rf {temp_dir}")
                return False, f"CounterStrikeSharp extraction failed: {stderr if stderr else 'Directory not created'}"
            
            await send_progress("✓ CounterStrikeSharp extracted successfully")
            
            # Clean up temp directory
            await self.execute_command(f"rm -rf {temp_dir}")
            
            # Verify installation
            css_dir = f"{cs2_dir}/game/csgo/addons/counterstrikesharp"
            verify_cmd = f"test -d {css_dir} && echo 'installed'"
            verify_success, verify_stdout, _ = await self.execute_command(verify_cmd)
            
            if verify_success and 'installed' in verify_stdout:
                await send_progress("=" * 60)
                await send_progress("✓ CounterStrikeSharp installed successfully!")
                await send_progress("=" * 60)
                await send_progress("NOTE: You need to restart your server for changes to take effect.")
                await send_progress("After restart, use 'meta list' and 'css_plugins list' to verify.")
                return True, "CounterStrikeSharp installed successfully"
            else:
                return False, "CounterStrikeSharp installation verification failed"
        
        except Exception as e:
            await send_progress(f"Installation error: {str(e)}")
            return False, f"Installation error: {str(e)}"
        finally:
            await self.disconnect()
    
    async def update_metamod(self, server: Server, progress_callback=None) -> Tuple[bool, str]:
        """
        Update Metamod:Source to the latest version
        
        Args:
            server: Server instance
            progress_callback: Optional async callback for progress updates
        Returns: (success: bool, message: str)
        """
        async def send_progress(message: str):
            """Helper to send progress updates"""
            if progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
                    await progress_callback(message)
                else:
                    progress_callback(message)
        
        await send_progress("Updating Metamod:Source to latest version...")
        await send_progress("This will reinstall Metamod with the latest version.")
        
        # Just reinstall - this will update to the latest version
        return await self.install_metamod(server, progress_callback)
    
    async def update_counterstrikesharp(self, server: Server, progress_callback=None) -> Tuple[bool, str]:
        """
        Update CounterStrikeSharp to the latest version
        
        Args:
            server: Server instance
            progress_callback: Optional async callback for progress updates
        Returns: (success: bool, message: str)
        """
        async def send_progress(message: str):
            """Helper to send progress updates"""
            if progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
                    await progress_callback(message)
                else:
                    progress_callback(message)
        
        await send_progress("Updating CounterStrikeSharp to latest version...")
        await send_progress("This will reinstall CounterStrikeSharp with the latest version.")
        
        # Just reinstall - this will update to the latest version
        return await self.install_counterstrikesharp(server, progress_callback)
    
    async def install_cs2fixes(self, server: Server, progress_callback=None) -> Tuple[bool, str]:
        """
        Install CS2Fixes for CS2 server
        
        Args:
            server: Server instance
            progress_callback: Optional async callback for progress updates
        Returns: (success: bool, message: str)
        """
        async def send_progress(message: str):
            """Helper to send progress updates"""
            if progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
                    await progress_callback(message)
                else:
                    progress_callback(message)
        
        success, msg = await self.connect(server)
        if not success:
            return False, f"Connection failed: {msg}"
        
        try:
            await send_progress("=" * 60)
            await send_progress("Installing CS2Fixes...")
            await send_progress("=" * 60)
            
            # Check if CS2 is installed
            cs2_dir = f"{server.game_directory}/cs2"
            check_cmd = f"test -d {cs2_dir} && echo 'exists'"
            check_success, check_stdout, _ = await self.execute_command(check_cmd)
            
            if not check_success or 'exists' not in check_stdout:
                return False, "CS2 server not found. Please deploy the server first."
            
            await send_progress("✓ CS2 server directory found")
            
            # Check if Metamod is installed
            metamod_dir = f"{cs2_dir}/game/csgo/addons/metamod"
            check_mm_cmd = f"test -d {metamod_dir} && echo 'exists'"
            check_mm_success, check_mm_stdout, _ = await self.execute_command(check_mm_cmd)
            
            if not check_mm_success or 'exists' not in check_mm_stdout:
                return False, "Metamod:Source is required but not found. Please install Metamod first."
            
            await send_progress("✓ Metamod:Source found")
            
            # Get latest CS2Fixes version from GitHub releases
            await send_progress("Fetching latest CS2Fixes version from GitHub...")
            
            get_latest_cmd = (
                "curl -sL https://api.github.com/repos/Source2ZE/CS2Fixes/releases/latest | "
                "grep -o '\"browser_download_url\": \"[^\"]*cs2fixes-linux[^\"]*\\.zip\"' | "
                "grep -o 'https://[^\"]*' | head -1"
            )
            success, cs2fixes_url, stderr = await self.execute_command(get_latest_cmd, timeout=30)
            
            if not success or not cs2fixes_url.strip():
                return False, "Failed to fetch latest CS2Fixes release from GitHub. Please check your internet connection."
            
            cs2fixes_url = cs2fixes_url.strip()
            await send_progress(f"✓ Found latest version: {cs2fixes_url}")
            
            # Create temp directory for download
            temp_dir = f"/tmp/cs2fixes_install_{server.id}"
            await send_progress(f"Creating temporary directory: {temp_dir}")
            await self.execute_command(f"mkdir -p {temp_dir}")
            
            # Download CS2Fixes
            await send_progress(f"Downloading CS2Fixes from {cs2fixes_url}...")
            download_cmd = f"curl -L -o {temp_dir}/cs2fixes.zip {cs2fixes_url} || wget -O {temp_dir}/cs2fixes.zip {cs2fixes_url}"
            success, stdout, stderr = await self.execute_command_streaming(
                download_cmd,
                output_callback=send_progress,
                timeout=180
            )
            
            # Verify the file was downloaded
            check_cmd = f"test -f {temp_dir}/cs2fixes.zip && echo 'exists'"
            check_success, check_stdout, _ = await self.execute_command(check_cmd)
            
            if not check_success or 'exists' not in check_stdout:
                await self.execute_command(f"rm -rf {temp_dir}")
                return False, f"CS2Fixes download failed. Please check the URL and try again."
            
            # Check file size to ensure it's not empty
            size_cmd = f"stat -f%z {temp_dir}/cs2fixes.zip 2>/dev/null || stat -c%s {temp_dir}/cs2fixes.zip 2>/dev/null"
            size_success, size_out, _ = await self.execute_command(size_cmd)
            if size_success and size_out.strip():
                file_size = int(size_out.strip())
                if file_size < self.MIN_EXPECTED_FILE_SIZE:
                    await self.execute_command(f"rm -rf {temp_dir}")
                    return False, f"Downloaded file is too small ({file_size} bytes). Download may have failed."
                await send_progress(f"✓ Downloaded {file_size} bytes")
            
            await send_progress("✓ CS2Fixes downloaded successfully")
            
            # Extract CS2Fixes to temp directory first
            await send_progress(f"Extracting CS2Fixes...")
            extract_cmd = f"unzip -o {temp_dir}/cs2fixes.zip -d {temp_dir}"
            success, stdout, stderr = await self.execute_command(extract_cmd, timeout=60)
            
            if not success:
                await self.execute_command(f"rm -rf {temp_dir}")
                return False, f"CS2Fixes extraction failed: {stderr}"
            
            await send_progress("✓ CS2Fixes extracted successfully")
            
            # Copy to CS2 addons directory
            csgo_dir = f"{cs2_dir}/game/csgo"
            await send_progress(f"Installing CS2Fixes to {csgo_dir}...")
            
            # The zip should contain an addons folder with cs2fixes
            copy_cmd = f"cp -r {temp_dir}/addons {csgo_dir}/"
            success, stdout, stderr = await self.execute_command(copy_cmd, timeout=60)
            
            if not success:
                await self.execute_command(f"rm -rf {temp_dir}")
                return False, f"Failed to copy CS2Fixes files: {stderr}"
            
            await send_progress("✓ CS2Fixes files copied successfully")
            
            # Clean up temp directory
            await self.execute_command(f"rm -rf {temp_dir}")
            
            # Verify installation
            cs2fixes_dir = f"{csgo_dir}/addons/cs2fixes"
            verify_cmd = f"test -d {cs2fixes_dir} && echo 'installed'"
            verify_success, verify_stdout, _ = await self.execute_command(verify_cmd)
            
            if verify_success and 'installed' in verify_stdout:
                await send_progress("=" * 60)
                await send_progress("✓ CS2Fixes installed successfully!")
                await send_progress("=" * 60)
                await send_progress("NOTE: You need to restart your server for changes to take effect.")
                await send_progress("Use 'meta list' command to verify CS2Fixes is loaded.")
                return True, "CS2Fixes installed successfully"
            else:
                return False, "CS2Fixes installation verification failed"
        
        except Exception as e:
            await send_progress(f"Installation error: {str(e)}")
            return False, f"Installation error: {str(e)}"
        finally:
            await self.disconnect()
    
    async def update_cs2fixes(self, server: Server, progress_callback=None) -> Tuple[bool, str]:
        """
        Update CS2Fixes to the latest version
        
        Args:
            server: Server instance
            progress_callback: Optional async callback for progress updates
        Returns: (success: bool, message: str)
        """
        async def send_progress(message: str):
            """Helper to send progress updates"""
            if progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
                    await progress_callback(message)
                else:
                    progress_callback(message)
        
        await send_progress("Updating CS2Fixes to latest version...")
        await send_progress("This will reinstall CS2Fixes with the latest version.")
        
        # Just reinstall - this will update to the latest version
        return await self.install_cs2fixes(server, progress_callback)
    
    async def list_directory(self, path: str, server: Server) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        List directory contents with file metadata
        
        Args:
            path: Directory path to list
            server: Server instance
        
        Returns:
            Tuple[bool, List[Dict], str]: (success, files_list, error_message)
            Each file dict contains: name, path, type, size, modified, permissions
        """
        if not self.conn:
            success, msg = await self.connect(server)
            if not success:
                return False, [], f"Connection failed: {msg}"
        
        try:
            # Use SFTP to list directory
            async with self.conn.start_sftp_client() as sftp:
                # Get directory listing
                files = []
                async for entry in sftp.scandir(path):
                    attrs = entry.attrs
                    file_info = {
                        'name': entry.filename,
                        'path': os.path.join(path, entry.filename),
                        'type': 'directory' if attrs.type == asyncssh.FILEXFER_TYPE_DIRECTORY else 'file',
                        'size': attrs.size or 0,
                        'modified': attrs.mtime or 0,
                        'permissions': oct(attrs.permissions)[-3:] if attrs.permissions else '000',
                        'is_symlink': attrs.type == asyncssh.FILEXFER_TYPE_SYMLINK
                    }
                    files.append(file_info)
                
                # Sort: directories first, then by name
                files.sort(key=lambda x: (x['type'] != 'directory', x['name'].lower()))
                
                return True, files, ""
        except asyncssh.SFTPError as e:
            return False, [], f"SFTP error: {str(e)}"
        except Exception as e:
            return False, [], f"Error listing directory: {str(e)}"
    
    async def read_file(self, file_path: str, server: Server, max_size: int = 10*1024*1024) -> Tuple[bool, str, str]:
        """
        Read file contents
        
        Args:
            file_path: Path to file
            server: Server instance
            max_size: Maximum file size to read (default 10MB)
        
        Returns:
            Tuple[bool, str, str]: (success, file_content, error_message)
        """
        if not self.conn:
            success, msg = await self.connect(server)
            if not success:
                return False, "", f"Connection failed: {msg}"
        
        try:
            async with self.conn.start_sftp_client() as sftp:
                # Check file size first
                attrs = await sftp.stat(file_path)
                if attrs.size > max_size:
                    return False, "", f"File too large ({attrs.size} bytes). Maximum size is {max_size} bytes."
                
                # Read file content
                async with sftp.open(file_path, 'r') as f:
                    content = await f.read()
                    # Try to decode as UTF-8, fallback to latin-1 if fails
                    try:
                        if isinstance(content, bytes):
                            text_content = content.decode('utf-8')
                        else:
                            text_content = content
                    except UnicodeDecodeError:
                        if isinstance(content, bytes):
                            text_content = content.decode('latin-1')
                        else:
                            text_content = content
                    
                    return True, text_content, ""
        except asyncssh.SFTPError as e:
            return False, "", f"SFTP error: {str(e)}"
        except Exception as e:
            return False, "", f"Error reading file: {str(e)}"
    
    async def write_file(self, file_path: str, content: str, server: Server) -> Tuple[bool, str]:
        """
        Write content to file
        
        Args:
            file_path: Path to file
            content: Content to write (string)
            server: Server instance
        
        Returns:
            Tuple[bool, str]: (success, error_message)
        """
        if not self.conn:
            success, msg = await self.connect(server)
            if not success:
                return False, f"Connection failed: {msg}"
        
        try:
            async with self.conn.start_sftp_client() as sftp:
                # Ensure parent directory exists
                parent_dir = os.path.dirname(file_path)
                if parent_dir:
                    try:
                        await sftp.stat(parent_dir)
                    except:
                        # Parent directory doesn't exist, create it
                        await sftp.makedirs(parent_dir)
                
                # Write file - use text mode and write string directly
                async with sftp.open(file_path, 'w', encoding='utf-8') as f:
                    await f.write(content)
                
                return True, ""
        except asyncssh.SFTPError as e:
            return False, f"SFTP error: {str(e)}"
        except Exception as e:
            return False, f"Error writing file: {str(e)}"
    
    async def delete_path(self, path: str, server: Server) -> Tuple[bool, str]:
        """
        Delete file or directory
        
        Args:
            path: Path to delete
            server: Server instance
        
        Returns:
            Tuple[bool, str]: (success, error_message)
        """
        if not self.conn:
            success, msg = await self.connect(server)
            if not success:
                return False, f"Connection failed: {msg}"
        
        try:
            async with self.conn.start_sftp_client() as sftp:
                attrs = await sftp.stat(path)
                
                if attrs.type == asyncssh.FILEXFER_TYPE_DIRECTORY:
                    # Remove directory recursively
                    await sftp.rmtree(path)
                else:
                    # Remove file
                    await sftp.remove(path)
                
                return True, ""
        except asyncssh.SFTPError as e:
            return False, f"SFTP error: {str(e)}"
        except Exception as e:
            return False, f"Error deleting: {str(e)}"
    
    async def create_directory(self, path: str, server: Server) -> Tuple[bool, str]:
        """
        Create directory
        
        Args:
            path: Directory path to create
            server: Server instance
        
        Returns:
            Tuple[bool, str]: (success, error_message)
        """
        if not self.conn:
            success, msg = await self.connect(server)
            if not success:
                return False, f"Connection failed: {msg}"
        
        try:
            async with self.conn.start_sftp_client() as sftp:
                await sftp.makedirs(path)
                return True, ""
        except asyncssh.SFTPError as e:
            return False, f"SFTP error: {str(e)}"
        except Exception as e:
            return False, f"Error creating directory: {str(e)}"
    
    async def rename_path(self, old_path: str, new_path: str, server: Server) -> Tuple[bool, str]:
        """
        Rename or move file/directory
        
        Args:
            old_path: Current path
            new_path: New path
            server: Server instance
        
        Returns:
            Tuple[bool, str]: (success, error_message)
        """
        if not self.conn:
            success, msg = await self.connect(server)
            if not success:
                return False, f"Connection failed: {msg}"
        
        try:
            async with self.conn.start_sftp_client() as sftp:
                await sftp.rename(old_path, new_path)
                return True, ""
        except asyncssh.SFTPError as e:
            return False, f"SFTP error: {str(e)}"
        except Exception as e:
            return False, f"Error renaming: {str(e)}"
    
    async def upload_file(self, local_path: str, remote_path: str, server: Server) -> Tuple[bool, str]:
        """
        Upload file from local to remote
        
        Args:
            local_path: Local file path
            remote_path: Remote file path
            server: Server instance
        
        Returns:
            Tuple[bool, str]: (success, error_message)
        """
        if not self.conn:
            success, msg = await self.connect(server)
            if not success:
                return False, f"Connection failed: {msg}"
        
        try:
            async with self.conn.start_sftp_client() as sftp:
                # Ensure parent directory exists
                parent_dir = os.path.dirname(remote_path)
                if parent_dir:
                    try:
                        await sftp.stat(parent_dir)
                    except:
                        await sftp.makedirs(parent_dir)
                
                # Upload file
                await sftp.put(local_path, remote_path)
                return True, ""
        except asyncssh.SFTPError as e:
            return False, f"SFTP error: {str(e)}"
        except Exception as e:
            return False, f"Error uploading file: {str(e)}"
    
    async def download_file(self, remote_path: str, local_path: str, server: Server) -> Tuple[bool, str]:
        """
        Download file from remote to local
        
        Args:
            remote_path: Remote file path
            local_path: Local file path
            server: Server instance
        
        Returns:
            Tuple[bool, str]: (success, error_message)
        """
        if not self.conn:
            success, msg = await self.connect(server)
            if not success:
                return False, f"Connection failed: {msg}"
        
        try:
            async with self.conn.start_sftp_client() as sftp:
                # Ensure local parent directory exists
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                
                # Download file
                await sftp.get(remote_path, local_path)
                return True, ""
        except asyncssh.SFTPError as e:
            return False, f"SFTP error: {str(e)}"
        except Exception as e:
            return False, f"Error downloading file: {str(e)}"
