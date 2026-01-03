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
    """Cooldown kontrolü"""
    cooldown_duration = 15 if is_vip else 30
    
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

def set_cooldown(user_id):
    """Cooldown ayarla"""
    try:
        cooldowns[str(user_id)] = datetime.now().isoformat()
        save_cooldowns(cooldowns)
    except:
        pass

# Periyodik temizlik görevi - 10 dakikada bir
@tasks.loop(minutes=10)
async def cleanup_task():
    """Her 10 dakikada bir eski kanalları ve cooldown'ları temizle"""
    try:
        guild = bot.get_guild(config.get('guild_id'))
        if not guild:
            return
        
        current_time = datetime.now()
        deleted_count = 0
        
        # 10 dakikadan eski kanalları sil
        for user_id, channel_id in list(user_channels.items()):
            try:
                if user_id in channel_creation_times:
                    creation_time = channel_creation_times[user_id]
                    time_passed = (current_time - creation_time).total_seconds() / 60
                    
                    if time_passed >= 10:
                        channel = guild.get_channel(channel_id)
                        if channel:
                            try:
                                await channel.delete(reason="10 dakika otomatik temizlik")
                                deleted_count += 1
                            except:
                                pass
                        
                        del user_channels[user_id]
                        del channel_creation_times[user_id]
                else:
                    channel = guild.get_channel(channel_id)
                    if not channel:
                        del user_channels[user_id]
            except:
                pass
        
        if deleted_count > 0:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 🧹 {deleted_count} kanal otomatik silindi")
        
        # Eski cooldown'ları temizle (7 gün eski)
        to_remove = []
        for user_id, last_use_str in list(cooldowns.items()):
            try:
                last_use = datetime.fromisoformat(last_use_str)
                if (current_time - last_use).days > 7:
                    to_remove.append(user_id)
            except:
                to_remove.append(user_id)
        
        for user_id in to_remove:
            del cooldowns[user_id]
        
        if to_remove:
            save_cooldowns(cooldowns)
            
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Temizlik hatası: {e}")

# Yeniden bağlanma mekanizması
@tasks.loop(minutes=5)
async def connection_check():
    """Her 5 dakikada bir bağlantıyı kontrol et"""
    if bot.is_closed():
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Bağlantı kesildi, yeniden bağlanılıyor...")

async def process_queue():
    """Öncelikli sıra sistemini işle"""
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
                        set_cooldown(current_job['user_id'])
                    except Exception as e:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ İş hatası: {e}")
                    finally:
                        with job_lock:
                            current_job = None
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Sıra hatası: {e}")
        
        await asyncio.sleep(1)

async def execute_sms_job(job):
    """SMS işini çalıştır"""
    try:
        interaction = job['interaction']
        tel_no = job['tel_no']
        mode = job['mode']
        hedef_sayi = job.get('kere', None)
        aralik = job.get('aralik', 1)
        user_type = job.get('user_type', 'normal')
        
        # Sadece başlangıç log'u
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ▶️  SMS başladı: {tel_no} | {hedef_sayi} adet | {mode.upper()} | {user_type.upper()}")
        
        embed = discord.Embed(
            title="📱 SMS Gönderimi Başladı",
            description=f"**Telefon:** `{tel_no}`\n**Mod:** {mode.upper()}\n**Hedef:** {hedef_sayi} SMS\n**Tip:** {user_type.upper()}",
            color=discord.Color.gold() if user_type == 'vip' else discord.Color.green()
        )
        embed.set_footer(text=f"Kullanıcı: {interaction.user.display_name}")
        
        try:
            status_msg = await interaction.followup.send(embed=embed, wait=True)
        except:
            return
        
        sms = SendSms(tel_no, "")
        basarili = 0
        basarisiz = 0
        lock = threading.Lock()
        gonderilen_toplam = 0
        
        def send_single_sms(fonk):
            nonlocal basarili, basarisiz, gonderilen_toplam
            try:
                getattr(sms, fonk)()
                with lock:
                    basarili += 1
                    gonderilen_toplam += 1
            except:
                with lock:
                    basarisiz += 1
                    gonderilen_toplam += 1
        
        if mode == 'normal':
            for fonk in servisler_sms:
                if gonderilen_toplam >= hedef_sayi:
                    break
                try:
                    getattr(sms, fonk)()
                    basarili += 1
                    gonderilen_toplam += 1
                except:
                    basarisiz += 1
                    gonderilen_toplam += 1
                
                # Her 20 SMS'te bir güncelle (daha az spam)
                if gonderilen_toplam % 20 == 0 or gonderilen_toplam >= hedef_sayi:
                    try:
                        embed.description = f"**Telefon:** `{tel_no}`\n**Mod:** NORMAL\n**Tip:** {user_type.upper()}\n**İlerleme:** {gonderilen_toplam}/{hedef_sayi}\n**Başarılı:** {basarili} | **Başarısız:** {basarisiz}"
                        await status_msg.edit(embed=embed)
                    except:
                        pass
                
                if gonderilen_toplam < hedef_sayi:
                    await asyncio.sleep(0.5)
        
        elif mode == 'turbo':
            index = 0
            while gonderilen_toplam < hedef_sayi:
                batch_size = min(10, hedef_sayi - gonderilen_toplam)
                threads = []
                
                for _ in range(batch_size):
                    if index >= len(servisler_sms):
                        index = 0
                    
                    fonk = servisler_sms[index]
                    index += 1
                    
                    try:
                        t = threading.Thread(target=lambda f=fonk: send_single_sms(f), daemon=True)
                        threads.append(t)
                        t.start()
                    except:
                        pass
                
                for t in threads:
                    try:
                        t.join(timeout=5)
                    except:
                        pass
                
                # Her 20 SMS'te bir güncelle (daha az spam)
                if gonderilen_toplam % 20 == 0 or gonderilen_toplam >= hedef_sayi:
                    try:
                        embed.description = f"**Telefon:** `{tel_no}`\n**Mod:** TURBO\n**Tip:** {user_type.upper()}\n**İlerleme:** {gonderilen_toplam}/{hedef_sayi}\n**Başarılı:** {basarili} | **Başarısız:** {basarisiz}"
                        await status_msg.edit(embed=embed)
                    except:
                        pass
                
                if gonderilen_toplam < hedef_sayi:
                    await asyncio.sleep(aralik)
        
        try:
            embed.title = "✅ SMS Gönderimi Tamamlandı"
            embed.color = discord.Color.blue()
            embed.description = f"**Telefon:** `{tel_no}`\n**Mod:** {mode.upper()}\n**Tip:** {user_type.upper()}\n**Hedef:** {hedef_sayi}\n**Gönderilen:** {gonderilen_toplam}\n**Başarılı:** {basarili}\n**Başarısız:** {basarisiz}"
            await status_msg.edit(embed=embed)
        except:
            pass
        
        # Sadece bitiş log'u
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ SMS tamamlandı: {tel_no} | {gonderilen_toplam}/{hedef_sayi} | Başarılı: {basarili}")
        
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Execute job hatası: {e}")

class ChannelButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="🔓 Kanal Aç", style=discord.ButtonStyle.green, custom_id="open_channel")
    async def open_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            user_id = interaction.user.id
            
            if user_id in user_channels:
                channel_id = user_channels[user_id]
                channel = interaction.guild.get_channel(channel_id)
                if channel:
                    await interaction.response.send_message(
                        f"❌ Zaten aktif bir kanalınız var: {channel.mention}",
                        ephemeral=True
                    )
                    return
                else:
                    del user_channels[user_id]
            
            category = interaction.guild.get_channel(config['category_id'])
            if not category or not isinstance(category, discord.CategoryChannel):
                await interaction.response.send_message(
                    "❌ Kategori bulunamadı!",
                    ephemeral=True
                )
                return
            
            await interaction.response.defer(ephemeral=True)
            
            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user: discord.PermissionOverwrite(
                    read_messages=True,
                    send_messages=True,
                    read_message_history=True
                ),
                interaction.guild.me: discord.PermissionOverwrite(
                    read_messages=True,
                    send_messages=True,
                    manage_channels=True
                )
            }
            
            channel_name = f"sms-{interaction.user.name}"
            channel = await category.create_text_channel(
                name=channel_name,
                overwrites=overwrites,
                topic=f"SMS kanalı - {interaction.user.display_name}"
            )
            
            user_channels[user_id] = channel.id
            channel_creation_times[user_id] = datetime.now()
            
            user_roles = [role.id for role in interaction.user.roles]
            is_vip = config['vip_role_id'] in user_roles
            
            embed = discord.Embed(
                title="🎉 Hoş Geldiniz!",
                description=f"Merhaba {interaction.user.mention}!\n\n"
                           f"**Kullanım Bilgileri:**\n"
                           f"{'• `/vipsms` komutu ile SMS gönderebilirsiniz' if is_vip else '• `/sms` komutu ile SMS gönderebilirsiniz'}\n"
                           f"• Maksimum SMS: **{'500' if is_vip else '100'}**\n"
                           f"• Mod: **{'Turbo (Otomatik)' if is_vip else 'Normal'}**\n"
                           f"• Cooldown: **{'15 dakika' if is_vip else '30 dakika'}**\n"
                           f"• Tip: **{'VIP ⭐' if is_vip else 'Normal'}**\n\n"
                           f"⚠️ **Bu kanal 10 dakika sonra otomatik silinecek!**",
                color=discord.Color.gold() if is_vip else discord.Color.green()
            )
            embed.set_footer(text="İyi kullanımlar!")
            
            await channel.send(embed=embed)
            await interaction.followup.send(
                f"✅ Kanalınız oluşturuldu: {channel.mention}",
                ephemeral=True
            )
            
            # Sadece kanal açılış log'u
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 📂 Kanal açıldı: {channel.name} | {interaction.user.name}")
            
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Kanal açma hatası: {e}")
            try:
                await interaction.followup.send(
                    "❌ Kanal oluşturulurken bir hata oluştu!",
                    ephemeral=True
                )
            except:
                pass

@bot.event
async def on_ready():
    try:
        print("=" * 50)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 🤖 Bot başlatıldı: {bot.user}")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 🌐 Sunucu sayısı: {len(bot.guilds)}")
        
        guild_id = config.get('guild_id')
        
        if guild_id:
            guild = discord.Object(id=guild_id)
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Komutlar senkronize edildi")
        else:
            await bot.tree.sync()
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Global komutlar senkronize edildi")
        
        guild = bot.get_guild(guild_id)
        if guild:
            button_channel = guild.get_channel(config['allowed_channel_id'])
            if button_channel and isinstance(button_channel, discord.TextChannel):
                try:
                    async for message in button_channel.history(limit=100):
                        if message.author == bot.user:
                            await message.delete()
                            await asyncio.sleep(0.5)
                except:
                    pass
                
                embed = discord.Embed(
                    title="📱 SMS Bot Sistemi",
                    description="**Hoş Geldiniz!**\n\n"
                               "SMS göndermek için aşağıdaki butona tıklayarak\n"
                               "size özel bir kanal açın.\n\n"
                               "**Normal Üyeler:**\n"
                               "• `/sms` komutu\n"
                               "• Maksimum 100 SMS\n"
                               "• Normal mod\n"
                               "• 30 dakika cooldown\n\n"
                               "**VIP Üyeler:**\n"
                               "• `/vipsms` komutu\n"
                               "• Maksimum 500 SMS\n"
                               "• Turbo mod (Otomatik)\n"
                               "• 15 dakika cooldown\n\n"
                               "⚠️ **Açılan kanallar 10 dakika sonra otomatik silinir!**",
                    color=discord.Color.blue()
                )
                embed.set_footer(text="Butona tıklayarak başlayın!")
                
                await button_channel.send(embed=embed, view=ChannelButton())
                print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Buton mesajı gönderildi")
        
        bot.loop.create_task(process_queue())
        cleanup_task.start()
        connection_check.start()
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Arka plan görevleri başlatıldı")
        
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="SMS Gönderimi 📱"
            ),
            status=discord.Status.online
        )
        
        print("=" * 50)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Bot 7/24 çalışmaya hazır!")
        print("=" * 50)
        
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Başlatma hatası: {e}")

@bot.event
async def on_disconnect():
    """Bağlantı kesildiğinde"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Discord bağlantısı kesildi")

@bot.event
async def on_resumed():
    """Bağlantı yeniden kurulduğunda"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Discord bağlantısı yeniden kuruldu")

@bot.tree.command(name="sms", description="Normal SMS gönderme komutu (Normal üyeler için)")
@app_commands.describe(
    numara="Telefon numarası (10 haneli)",
    sayi="Gönderilecek SMS sayısı (1-500 arası)"
)
@app_commands.default_permissions(send_messages=True)
async def sms_command(interaction: discord.Interaction, numara: str, sayi: int):
    try:
        user_roles = [role.id for role in interaction.user.roles]
        
        if config['vip_role_id'] in user_roles:
            await interaction.response.send_message(
                "❌ VIP üyeler `/vipsms` komutunu kullanmalıdır!",
                ephemeral=True
            )
            return
        
        if config['normal_role_id'] not in user_roles:
            await interaction.response.send_message(
                "❌ Bu komutu kullanmak için Normal Üye rolüne sahip olmalısınız!",
                ephemeral=True
            )
            return
        
        if interaction.user.id not in user_channels or user_channels[interaction.user.id] != interaction.channel.id:
            await interaction.response.send_message(
                "❌ Bu komutu sadece size özel açılan kanalda kullanabilirsiniz!",
                ephemeral=True
            )
            return
        
        can_use, error_msg = check_cooldown(interaction.user.id, is_vip=False)
        if not can_use:
            await interaction.response.send_message(error_msg, ephemeral=True)
            return
        
        if not numara or not numara.isdigit() or len(numara) != 10:
            await interaction.response.send_message(
                "❌ Geçersiz telefon numarası! 10 haneli numara giriniz (örn: 5551234567)",
                ephemeral=True
            )
            return
        
        if sayi < 1 or sayi > 100:
            await interaction.response.send_message(
                "❌ Normal üyeler için sayı 1-100 arasında olmalıdır!",
                ephemeral=True
            )
            return
        
        await interaction.response.defer()
        
        job = {
            'interaction': interaction,
            'tel_no': numara,
            'mode': 'normal',
            'kere': sayi,
            'aralik': 1,
            'user_type': 'normal',
            'user_id': interaction.user.id
        }
        
        job_queue.put((2, job))
        queue_size = job_queue.qsize()
        
        embed = discord.Embed(
            title="⏳ İş Sıraya Eklendi",
            description=f"**Telefon:** `{numara}`\n**Sıra Pozisyonu:** {queue_size}\n**Mod:** NORMAL\n**Hedef SMS:** {sayi}\n**Tip:** NORMAL\n**Durum:** Beklemede...",
            color=discord.Color.orange()
        )
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Komut hatası: {e}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Bir hata oluştu!", ephemeral=True)
            else:
                await interaction.followup.send("❌ Bir hata oluştu!", ephemeral=True)
        except:
            pass

@bot.tree.command(name="vipsms", description="VIP SMS gönderme komutu (VIP üyeler için - Otomatik Turbo)")
@app_commands.describe(
    numara="Telefon numarası (10 haneli)",
    sayi="Gönderilecek SMS sayısı (1-500 arası)"
)
@app_commands.default_permissions(send_messages=True)
async def vipsms_command(interaction: discord.Interaction, numara: str, sayi: int):
    try:
        user_roles = [role.id for role in interaction.user.roles]
        
        if config['vip_role_id'] not in user_roles:
            await interaction.response.send_message(
                "❌ Bu komutu kullanmak için VIP Üye rolüne sahip olmalısınız!",
                ephemeral=True
            )
            return
        
        if interaction.user.id not in user_channels or user_channels[interaction.user.id] != interaction.channel.id:
            await interaction.response.send_message(
                "❌ Bu komutu sadece size özel açılan kanalda kullanabilirsiniz!",
                ephemeral=True
            )
            return
        
        can_use, error_msg = check_cooldown(interaction.user.id, is_vip=True)
        if not can_use:
            await interaction.response.send_message(error_msg, ephemeral=True)
            return
        
        if not numara or not numara.isdigit() or len(numara) != 10:
            await interaction.response.send_message(
                "❌ Geçersiz telefon numarası! 10 haneli numara giriniz (örn: 5551234567)",
                ephemeral=True
            )
            return
        
        if sayi < 1 or sayi > 500:
            await interaction.response.send_message(
                "❌ VIP üyeler için sayı 1-500 arasında olmalıdır!",
                ephemeral=True
            )
            return
        
        await interaction.response.defer()
        
        job = {
            'interaction': interaction,
            'tel_no': numara,
            'mode': 'turbo',
            'kere': sayi,
            'aralik': 0.5,
            'user_type': 'vip',
            'user_id': interaction.user.id
        }
        
        job_queue.put((1, job))
        queue_size = job_queue.qsize()
        
        embed = discord.Embed(
            title="⏳ VIP İş Sıraya Eklendi",
            description=f"**Telefon:** `{numara}`\n**Sıra Pozisyonu:** {queue_size}\n**Mod:** TURBO (Otomatik)\n**Hedef SMS:** {sayi}\n**Tip:** VIP ⭐\n**Durum:** Öncelikli beklemede...",
            color=discord.Color.gold()
        )
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ VIP komut hatası: {e}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Bir hata oluştu!", ephemeral=True)
            else:
                await interaction.followup.send("❌ Bir hata oluştu!", ephemeral=True)
        except:
            pass

@bot.event
async def on_error(event, *args, **kwargs):
    """Global hata yakalama - Sessiz"""
    pass

if __name__ == "__main__":
    token = config.get('bot_token')
    if not token or token == "":
        token = os.getenv('BOT_TOKEN')
        
    if token:
        bot.run(token)
    else:
        sys.__stdout__.write("❌ HATA: Token bulunamadı! Lütfen config.json veya Koyeb ayarlarına token ekleyin.\n")

        import traceback
        traceback.print_exc()

