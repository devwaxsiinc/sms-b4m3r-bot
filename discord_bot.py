import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
import asyncio
import threading
from queue import PriorityQueue
from sms import SendSms
import sys
import logging
from datetime import datetime, timedelta
import os

# Logları tamamen kapat
logging.disable(logging.CRITICAL)
sys.stdout = open(os.devnull, 'w') if '--silent' in sys.argv else sys.stdout

# Config dosyasını yükle
try:
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
except Exception as e:
    print("❌ Config dosyası yüklenemedi!")
    sys.exit(1)

# Cooldown dosyası
COOLDOWN_FILE = 'cooldowns.json'

def load_cooldowns():
    if os.path.exists(COOLDOWN_FILE):
        try:
            with open(COOLDOWN_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_cooldowns(cooldowns):
    try:
        with open(COOLDOWN_FILE, 'w') as f:
            json.dump(cooldowns, f)
    except:
        pass

cooldowns = load_cooldowns()
user_channels = {}
channel_creation_times = {}

# Bot ayarları
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Öncelikli sıra sistemi
job_queue = PriorityQueue()
current_job = None
job_lock = threading.Lock()

# SMS servisleri
servisler_sms = []
try:
    for attribute in dir(SendSms):
        attribute_value = getattr(SendSms, attribute)
        if callable(attribute_value) and not attribute.startswith('__'):
            servisler_sms.append(attribute)
except:
    pass

def check_cooldown(user_id, is_vip=False):
    """Cooldown kontrolü - YENİ KURALLAR"""
    if is_vip:
        return True, None # VIP için Cooldown yok!
    
    cooldown_duration = 60 # Normal üye için 60 dakika
    
    if str(user_id) in cooldowns:
        try:
            last_use = datetime.fromisoformat(cooldowns[str(user_id)])
            time_passed = datetime.now() - last_use
            remaining = timedelta(minutes=cooldown_duration) - time_passed
            
            if remaining.total_seconds() > 0:
                minutes = int(remaining.total_seconds() // 60)
                seconds = int(remaining.total_seconds() % 60)
                return False, f"⏰ Cooldown aktif! Kalan süre: **{minutes} dakika {seconds} saniye**"
        except:
            pass
    
    return True, None

def set_cooldown(user_id, is_vip=False):
    """Cooldown ayarla - VIP ise kaydetme"""
    if is_vip:
        return # VIP ise cooldown listesine ekleme yapma
    try:
        cooldowns[str(user_id)] = datetime.now().isoformat()
        save_cooldowns(cooldowns)
    except:
        pass

@tasks.loop(minutes=10)
async def cleanup_task():
    try:
        guild = bot.get_guild(config.get('guild_id'))
        if not guild: return
        current_time = datetime.now()
        deleted_count = 0
        for user_id, channel_id in list(user_channels.items()):
            try:
                if user_id in channel_creation_times:
                    creation_time = channel_creation_times[user_id]
                    time_passed = (current_time - creation_time).total_seconds() / 60
                    if time_passed >= 10:
                        channel = guild.get_channel(channel_id)
                        if channel:
                            await channel.delete(reason="10 dakika otomatik temizlik")
                            deleted_count += 1
                        del user_channels[user_id]
                        del channel_creation_times[user_id]
            except: pass
        if deleted_count > 0:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 🧹 {deleted_count} kanal silindi")
    except Exception as e:
        print(f"❌ Temizlik hatası: {e}")

async def process_queue():
    global current_job
    while True:
        try:
            if not job_queue.empty():
                with job_lock:
                    if current_job is None:
                        priority, job = job_queue.get()
                        current_job = job
                if current_job:
                    try:
                        await execute_sms_job(current_job)
                        is_vip = current_job.get('user_type') == 'vip'
                        set_cooldown(current_job['user_id'], is_vip=is_vip)
                    except Exception as e:
                        print(f"❌ İş hatası: {e}")
                    finally:
                        with job_lock:
                            current_job = None
        except Exception as e:
            print(f"❌ Sıra hatası: {e}")
        await asyncio.sleep(1)

async def execute_sms_job(job):
    try:
        interaction = job['interaction']
        tel_no = job['tel_no']
        mode = job['mode']
        hedef_sayi = job.get('kere', 1)
        user_type = job.get('user_type', 'normal')
        
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ▶️ SMS başladı: {tel_no} | {hedef_sayi} adet | {mode.upper()}")
        
        embed = discord.Embed(
            title="📱 SMS Gönderimi Başladı",
            description=f"**Telefon:** `{tel_no}`\n**Mod:** {mode.upper()}\n**Hedef:** {hedef_sayi} SMS\n**Tip:** {user_type.upper()}",
            color=discord.Color.gold() if user_type == 'vip' else discord.Color.green()
        )
        status_msg = await interaction.followup.send(embed=embed, wait=True)
        
        sms = SendSms(tel_no, "")
        basarili, basarisiz, gonderilen_toplam = 0, 0, 0
        lock = threading.Lock()
        
        def send_single_sms(fonk):
            nonlocal basarili, basarisiz, gonderilen_toplam
            try:
                getattr(sms, fonk)()
                with lock: basarili += 1; gonderilen_toplam += 1
            except:
                with lock: basarisiz += 1; gonderilen_toplam += 1

        if mode == 'normal':
            for fonk in servisler_sms:
                if gonderilen_toplam >= hedef_sayi: break
                try:
                    getattr(sms, fonk)()
                    basarili += 1; gonderilen_toplam += 1
                except:
                    basarisiz += 1; gonderilen_toplam += 1
                if gonderilen_toplam % 10 == 0:
                    embed.description = f"**Telefon:** `{tel_no}`\n**İlerleme:** {gonderilen_toplam}/{hedef_sayi}\n**Başarılı:** {basarili}"
                    await status_msg.edit(embed=embed)
                await asyncio.sleep(0.5)
        
        elif mode == 'turbo':
            index = 0
            while gonderilen_toplam < hedef_sayi:
                batch_size = min(15, hedef_sayi - gonderilen_toplam)
                threads = []
                for _ in range(batch_size):
                    if index >= len(servisler_sms): index = 0
                    t = threading.Thread(target=send_single_sms, args=(servisler_sms[index],), daemon=True)
                    threads.append(t); t.start(); index += 1
                for t in threads: t.join(timeout=3)
                embed.description = f"**Telefon:** `{tel_no}`\n**Turbo Mod Aktif**\n**İlerleme:** {gonderilen_toplam}/{hedef_sayi}"
                await status_msg.edit(embed=embed)
                await asyncio.sleep(0.1)

        embed.title = "✅ SMS Gönderimi Tamamlandı"
        embed.color = discord.Color.blue()
        embed.description = f"**Telefon:** `{tel_no}`\n**Toplam Gönderilen:** {gonderilen_toplam}\n**Başarılı:** {basarili}"
        await status_msg.edit(embed=embed)
    except Exception as e:
        print(f"❌ Execute job hatası: {e}")

class ChannelButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="🔓 Kanal Aç", style=discord.ButtonStyle.green, custom_id="open_channel")
    async def open_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        if user_id in user_channels:
            await interaction.response.send_message("❌ Zaten aktif bir kanalınız var!", ephemeral=True); return
        
        category = interaction.guild.get_channel(config['category_id'])
        await interaction.response.defer(ephemeral=True)
        
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            interaction.guild.me: discord.PermissionOverwrite(read_messages=True, manage_channels=True)
        }
        channel = await category.create_text_channel(name=f"sms-{interaction.user.name}", overwrites=overwrites)
        user_channels[user_id] = channel.id
        channel_creation_times[user_id] = datetime.now()
        
        is_vip = config['vip_role_id'] in [role.id for role in interaction.user.roles]
        
        embed = discord.Embed(
            title="🎉 Hoş Geldiniz!",
            description=f"Merhaba {interaction.user.mention}!\n\n"
                        f"**Kullanım Bilgileri:**\n"
                        f"• Komut: **{'/vipsms' if is_vip else '/sms'}**\n"
                        f"• Maksimum SMS: **{'500' if is_vip else '30'}**\n"
                        f"• Mod: **{'Turbo (Otomatik)' if is_vip else 'Normal'}**\n"
                        f"• Cooldown: **{'YOK! 🔥' if is_vip else '60 dakika'}**\n"
                        f"• Tip: **{'VIP ⭐' if is_vip else 'Normal'}**\n\n"
                        f"⚠️ **Kanal 10 dk sonra silinir.**",
            color=discord.Color.gold() if is_vip else discord.Color.green()
        )
        await channel.send(embed=embed)
        await interaction.followup.send(f"✅ Kanal açıldı: {channel.mention}", ephemeral=True)

@bot.event
async def on_ready():
    # --- İSTEDİĞİN GÖRSELDEKİ DURUMU BURAYA EKLEDİM ---
    await bot.change_presence(
        status=discord.Status.dnd, 
        activity=discord.CustomActivity(name="SMS GÖNDERİYORUM RAHATSIZ ETME")
    )
    # ------------------------------------------------

    guild_id = config.get('guild_id')
    guild = discord.Object(id=guild_id)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    
    channel = bot.get_channel(config['allowed_channel_id'])
    if channel:
        async for msg in channel.history(limit=10):
            if msg.author == bot.user: await msg.delete()
        
        embed = discord.Embed(
            title="📱 SMS Bot Sistemi",
            description="**Üyelik Özellikleri:**\n\n"
                        "**Normal Üyeler:**\n"
                        "• `/sms` komutu\n"
                        "• Maksimum 30 SMS\n"
                        "• Normal mod\n"
                        "• 60 dakika cooldown\n\n"
                        "**VIP Üyeler:**\n"
                        "• `/vipsms` komutu\n"
                        "• Maksimum 500 SMS\n"
                        "• Turbo mod (Otomatik)\n"
                        "• **Cooldown Yok!**\n\n"
                        "👇 Başlamak için butona tıkla!",
            color=discord.Color.blue()
        )
        await channel.send(embed=embed, view=ChannelButton())
    
    bot.loop.create_task(process_queue())
    cleanup_task.start()
    sys.__stdout__.write(f"✅ Bot hazır: {bot.user}\n")

@bot.tree.command(name="sms", description="Normal SMS (Maks 30)")
async def sms_command(interaction: discord.Interaction, numara: str, sayi: int):
    user_roles = [role.id for role in interaction.user.roles]
    if config['vip_role_id'] in user_roles:
        await interaction.response.send_message("❌ VIP iseniz lütfen `/vipsms` kullanın!", ephemeral=True); return
    
    if interaction.channel.id != user_channels.get(interaction.user.id):
        await interaction.response.send_message("❌ Sadece özel kanalınızda kullanabilirsiniz!", ephemeral=True); return

    can_use, msg = check_cooldown(interaction.user.id, False)
    if not can_use:
        await interaction.response.send_message(msg, ephemeral=True); return

    if sayi < 1 or sayi > 30:
        await interaction.response.send_message("❌ Limit: 1-30 SMS.", ephemeral=True); return

    await interaction.response.defer()
    job_queue.put((2, {'interaction': interaction, 'tel_no': numara, 'mode': 'normal', 'kere': sayi, 'user_type': 'normal', 'user_id': interaction.user.id}))

@bot.tree.command(name="vipsms", description="VIP SMS (Maks 500 - Turbo)")
async def vipsms_command(interaction: discord.Interaction, numara: str, sayi: int):
    if config['vip_role_id'] not in [role.id for role in interaction.user.roles]:
        await interaction.response.send_message("❌ VIP olmalısınız!", ephemeral=True); return
    
    if interaction.channel.id != user_channels.get(interaction.user.id):
        await interaction.response.send_message("❌ Sadece özel kanalınızda kullanabilirsiniz!", ephemeral=True); return

    if sayi < 1 or sayi > 500:
        await interaction.response.send_message("❌ VIP Limit: 1-500 SMS.", ephemeral=True); return

    await interaction.response.defer()
    job_queue.put((1, {'interaction': interaction, 'tel_no': numara, 'mode': 'turbo', 'kere': sayi, 'user_type': 'vip', 'user_id': interaction.user.id}))

if __name__ == "__main__":
    token = config.get('bot_token')
    if not token or token == "":
        token = os.getenv('BOT_TOKEN')
        
    if token:
        bot.run(token)
    else:
        sys.__stdout__.write("❌ HATA: Token bulunamadı! Lütfen config.json veya Koyeb ayarlarına token ekleyin.\n")
