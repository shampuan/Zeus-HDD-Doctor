import sys
import subprocess
import os
import re
from colorama import init, Fore, Style # Renkli çıktı için

# Renkli çıktıları başlat
init(autoreset=True)

# --- Temel Fonksiyonlar ---

def clear_screen():
    """Terminal ekranını temizler."""
    os.system('clear') # Linux'ta ekranı temizleme komutu 'clear'dır

def print_header(title):
    """Program başlığını ve çerçevesini yazdırır."""
    clear_screen()
    print(Style.BRIGHT + "==========================================")
    print(f"       {title.upper()}    ")
    print("==========================================" + Style.RESET_ALL)

def print_separator():
    """Çıktılar arasında ayırıcı bir çizgi çizer."""
    print(Fore.BLUE + "-" * 50 + Style.RESET_ALL)

def get_disk_list_linux():
    """
    Linux sistemindeki fiziksel diskleri lsblk kullanarak listeler.
    """
    disks = []
    try:
        # lsblk -o NAME,SIZE,TYPE,MODEL,VENDOR -n:
        # -o: Çıktı formatını belirler (İsim, Boyut, Tip, Model, Üretici)
        # -n: Başlıkları göstermez
        output = subprocess.check_output(['lsblk', '-o', 'NAME,SIZE,TYPE,MODEL,VENDOR', '-n'], stderr=subprocess.PIPE).decode('utf-8')
        for line in output.splitlines():
            parts = line.strip().split()
            if len(parts) >= 3 and parts[2] == "disk": # Sadece 'disk' tipindeki cihazları al
                disk_name = parts[0] # sda, sdb gibi
                disk_size = parts[1] # 1T, 500G gibi
                
                # Model ve Vendor'ı birleştir
                model_vendor_parts = parts[3:] if len(parts) > 3 else []
                full_model_vendor = " ".join(model_vendor_parts).strip()

                full_name = f"{disk_name} ({disk_size}) - {full_model_vendor}".strip()
                disks.append({'path': f"/dev/{disk_name}", 'name': full_name})
        return disks
    except FileNotFoundError:
        print(Fore.RED + "Hata: 'lsblk' komutu bulunamadı. Lütfen 'util-linux' paketinin yüklü olduğundan emin olun." + Style.RESET_ALL)
        return []
    except subprocess.CalledProcessError as e:
        print(Fore.RED + f"Hata: 'lsblk' çalıştırılırken sorun oluştu: {e.stderr.decode('utf-8', errors='ignore').strip()}" + Style.RESET_ALL)
        return []
    except Exception as e:
        print(Fore.RED + f"Hata: Disk listeleme başarısız oldu: {e}" + Style.RESET_ALL)
        return []

def get_smart_data_linux(disk_path):
    """
    Linux için smartctl komutunu kullanarak belirtilen diskin SMART verilerini alır.
    """
    attributes_output = None
    info_output = None
    error_message = ""

    # smartctl genellikle cihaz yolunu ve aygıt tipini otomatik olarak algılar.
    # Ancak bazı durumlarda -d parametresi gerekebilir. Yaygın tipleri deneyelim.
    device_types = ['auto', 'sat', 'nvme', 'scsi'] # 'auto' genellikle yeterlidir

    for dev_type in device_types:
        try:
            print(f"  {Fore.YELLOW}-> smartctl -A -d {dev_type} {disk_path} deneniyor..." + Style.RESET_ALL)
            # -A: SMART verileri
            attributes_output = subprocess.check_output(['smartctl', '-A', '-d', dev_type, disk_path], stderr=subprocess.PIPE, timeout=30).decode('utf-8', errors='ignore')

            print(f"  {Fore.YELLOW}-> smartctl -i -d {dev_type} {disk_path} deneniyor..." + Style.RESET_ALL)
            # -i: Cihaz bilgileri
            info_output = subprocess.check_output(['smartctl', '-i', '-d', dev_type, disk_path], stderr=subprocess.PIPE, timeout=30).decode('utf-8', errors='ignore')

            # SMART desteği kapalı ise özel bir hata mesajı dön
            if "SMART support is: Disabled" in info_output or "SMART Disabled" in info_output:
                error_message = f"Disk '{disk_path}' SMART özelliğini desteklemiyor veya devre dışı."
                return None, None, error_message # Bu özel hata durumu için döngüyü kır
            
            return attributes_output, info_output, "" # Başarılı dönüş
        except FileNotFoundError:
            error_message = Fore.RED + "Hata: 'smartctl' komutu bulunamadı. Lütfen 'smartmontools' paketinin yüklü olduğundan emin olun." + Style.RESET_ALL
            return None, None, error_message # smartctl yoksa hiçbiri çalışmaz, direkt çık
        except subprocess.CalledProcessError as e:
            # Hata mesajını daha okunur hale getir
            stderr_output = e.stderr.decode('utf-8', errors='ignore').strip()
            if "SCSI error" in stderr_output or "Error SMART" in stderr_output:
                 error_message = Fore.YELLOW + f"smartctl '{dev_type}' tipiyle '{disk_path}' için çalıştırılamadı (hata: {stderr_output[:100]}...). Başka tip deneniyor." + Style.RESET_ALL
            else:
                error_message = Fore.RED + f"smartctl '{dev_type}' tipiyle '{disk_path}' için çalıştırılamadı. Hata: {stderr_output}" + Style.RESET_ALL
            # Diğer tipleri denemek için hatayı geç, bu hatayı son dönüşte kullanırız
        except subprocess.TimeoutExpired:
            error_message = Fore.RED + f"smartctl '{dev_type}' tipiyle '{disk_path}' için zaman aşımına uğradı." + Style.RESET_ALL
        except Exception as e:
            error_message = Fore.RED + f"Bilinmeyen bir hata oluştu: {e}" + Style.RESET_ALL

    # Tüm tipler denendi ve başarısız oldu
    return None, None, error_message if error_message else Fore.RED + f"Disk '{disk_path}' için SMART verileri alınamadı veya desteklenmiyor." + Style.RESET_ALL


def parse_smart_attributes(smart_attributes_output):
    """
    smartctl -A çıktısını ayrıştırarak SMART özniteliklerini bir sözlük listesi olarak döndürür.
    """
    attributes = []

    attribute_pattern = re.compile(
        r'^\s*(\d+)\s+([a-zA-Z0-9_]+)\s+'     # 1: ID, 2: Name
        r'(\S+)\s+'                         # 3: Flags
        r'(\d+)\s+'                         # 4: Current Value
        r'(\d+)\s+'                         # 5: Worst Value
        r'(\d+)\s+'                         # 6: Threshold Value
        r'(\S+)\s+'                         # 7: Type
        r'(\S+)\s+'                         # 8: Updated
        r'(\S+)\s+'                         # 9: When_Failed
        r'([-]?\d+)$'                       # 10: Raw_Value (Negatif değerler de olabilir)
    )

    start_parsing = False
    for line in smart_attributes_output.splitlines():
        if "ID# ATTRIBUTE_NAME" in line:
            start_parsing = True
            continue
        if start_parsing:
            # Raporun sonunu belirten satırları kontrol et
            if line.strip() == "" or "SMART Error Log" in line or "SMART Self-test Log" in line or "Vendor Specific SMART Attributes" in line:
                break

            match = attribute_pattern.match(line)
            if match:
                try:
                    attr = {
                        "ID": int(match.group(1)),
                        "Name": match.group(2),
                        "Current": int(match.group(4)),
                        "Worst": int(match.group(5)),
                        "Threshold": int(match.group(6)),
                        "Type": match.group(7),
                        "Updated": match.group(8),
                        "Raw_Value": int(match.group(10))
                    }
                    attributes.append(attr)
                except ValueError:
                    # Raw Value boş veya tire (-) ise 0 olarak al
                    raw_val_str = match.group(10)
                    try:
                        attr = {
                            "ID": int(match.group(1)),
                            "Name": match.group(2),
                            "Current": int(match.group(4)),
                            "Worst": int(match.group(5)),
                            "Threshold": int(match.group(6)),
                            "Type": match.group(7),
                            "Updated": match.group(8),
                            "Raw_Value": 0 if raw_val_str.strip() == '-' or not raw_val_str.strip() else int(raw_val_str)
                        }
                        attributes.append(attr)
                    except Exception as ve:
                        # Ayrıştırma hatası olursa yoksay veya logla
                        # print(f"Hata: SMART öznitelik satırı ayrıştırılamadı: {line} - {ve}")
                        pass
            else:
                pass # Eşleşmeyen satırları (boş satırlar, başlıklar vb.) atla
    return attributes


def parse_smart_info(smart_info_output):
    """
    smartctl -i çıktısından disk bilgilerini ayrıştırır.
    """
    info = {}
    lines = smart_info_output.splitlines()
    for line in lines:
        if "Model Family:" in line:
            info["Model Family"] = line.split(":", 1)[1].strip()
        elif "Device Model:" in line:
            info["Device Model"] = line.split(":", 1)[1].strip()
        elif "Serial Number:" in line:
            info["Serial Number"] = line.split(":", 1)[1].strip()
        elif "Firmware Version:" in line:
            info["Firmware Version"] = line.split(":", 1)[1].strip()
        elif "User Capacity:" in line:
            match = re.search(r'\[(.*?)\]', line) # Köşeli parantez içindeki kapasiteyi al
            info["User Capacity"] = match.group(1) if match else line.split(":", 1)[1].strip().split("bytes")[0].strip()
        elif "Rotation Rate:" in line:
            info["Rotation Rate"] = line.split(":", 1)[1].strip()
        elif "SMART support is:" in line:
            info["SMART Supported"] = "Enabled" if "Enabled" in line else "Disabled"
        elif "Local Time is:" in line:
            info["Local Time"] = line.split(":", 1)[1].strip()
        elif "Power On Hours:" in line:
            match = re.search(r'(\d+)\s+hours', line)
            if match:
                info["Power On Hours"] = match.group(1) + " hours"
        elif "Power Cycle Count:" in line:
            match = re.search(r'(\d+)', line)
            if match:
                info["Power Cycle Count"] = match.group(1)
        elif "Wear_Leveling_Count" in line: # SSD'ler için
             match = re.match(r'.*Wear_Leveling_Count\s+.*?\s+(\d+)', line)
             if match:
                 info["Wear Leveling"] = match.group(1)
        elif "Media_Wearout_Indicator" in line: # SSD'ler için
             match = re.match(r'.*Media_Wearout_Indicator\s+.*?\s+(\d+)', line)
             if match:
                 info["Media Wearout"] = match.group(1)
        elif "Data Units Written:" in line:
            info["Data Units Written"] = line.split(":", 1)[1].strip()
        elif "Data Units Read:" in line:
            info["Data Units Read"] = line.split(":", 1)[1].strip()
    return info

def calculate_health_score(attributes, disk_info, smart_data_available):
    """
    SMART özniteliklerine göre basit bir sağlık puanı hesaplar (0-100).
    smart_data_available: SMART verisine erişilip erişilemediğini belirtir.
    """
    score = 100
    warnings = []
    
    if not smart_data_available:
        # SMART verisine erişilemiyorsa özel durum
        health_status = Style.DIM + Fore.WHITE + "BİLİNMİYOR" + Style.RESET_ALL # Gri tonu
        notes = "Aygıtın SMART verilerine erişilemediği için sağlık durumu bilinmiyor."
        return "Bilinmiyor", health_status, notes # Puan olarak "Bilinmiyor" stringi döndür

    # Kritik Raw Value'a sahip SMART Öznitelik ID'leri
    critical_raw_value_attributes_ids = {
        1,   # Raw Read Error Rate
        5,   # Reallocated Sector Count
        7,   # Seek Error Rate
        196, # Reallocated Event Count
        197, # Current Pending Sector Count
        198, # Uncorrectable Sector Count
        199  # UDMA CRC Error Count
    }

    for attr in attributes:
        # 1. Eşik Değer Kontrolü (Threshold)
        # Threshold > 0 ve Current değer Threshold'dan küçükse
        if attr["Threshold"] > 0 and attr["Current"] < attr["Threshold"]:
            score -= 15
            warnings.append(f"'{attr['Name']}' (ID:{attr['ID']}) kritik eşik ({attr['Threshold']}) altında ({attr['Current']})!")

        # 2. Kritik Raw Value Kontrolü (Raw_Value'nun 0'dan büyük olması)
        if attr["ID"] in critical_raw_value_attributes_ids and attr["Raw_Value"] > 0:
            score -= 10
            warnings.append(f"'{attr['Name']}' (ID:{attr['ID']}) Raw Value'u 0'dan büyük ({attr['Raw_Value']})!")

        # 3. Sıcaklık Kontrolü
        if attr["ID"] == 194 or "Temperature" in attr["Name"]: # ID 194 genellikle sıcaklık, bazı disklerde isimde de geçebilir
            current_temp = attr["Raw_Value"] if attr["ID"] == 194 else attr["Current"] # Raw_Value veya Current'i kullan
            if current_temp > 50:
                score -= 5
                warnings.append(f"Disk sıcaklığı yüksek ({current_temp}°C).")
            elif current_temp > 60:
                score -= 15
                warnings.append(f"DİKKAT: Disk sıcaklığı çok yüksek ({current_temp}°C)!")

        # 4. SSD Sağlığı (Wear Leveling ve Media Wearout Indicator)
        # ID 177: Wear_Leveling_Count (SSD'nin ne kadar yıprandığını gösterir, yüksek değerler kötü olabilir)
        if attr["ID"] == 177 and attr["Raw_Value"] > 0:
            if attr["Raw_Value"] > 50000: # Örnek bir eşik, üreticiye göre değişebilir
                score -= 5
                warnings.append(f"SSD yıpranma düzeyi yüksek: {attr['Raw_Value']} (Wear_Leveling_Count).")
        # ID 233: Media_Wearout_Indicator (SSD'nin kalan ömrü yüzdesi, 100 en iyi, 0 en kötü)
        elif attr["ID"] == 233 and attr["Raw_Value"] < 100:
            if attr["Raw_Value"] < 20:
                score -= 20
                warnings.append(f"SSD yıpranma düzeyi kritik: %{attr['Raw_Value']} (Media_Wearout_Indicator).")
            elif attr["Raw_Value"] < 50:
                score -= 10
                warnings.append(f"SSD yıpranma düzeyi yüksek: %{attr['Raw_Value']} (Media_Wearout_Indicator).")

    score = max(0, min(100, score)) # Puanı 0-100 arasına sıkıştır

    health_status = ""
    notes = ""
    # Derecelendirme ve Renklendirme (Güncel Taleplere Göre)
    if score > 85: # Mükemmel Durum (Çok Parlak Yeşil)
        health_status = Style.BRIGHT + Fore.LIGHTGREEN_EX + "MÜKEMMEL" + Style.RESET_ALL
        notes = "Disk durumu MÜKEMMEL. Herhangi bir işlem gerekli değildir."
    elif score > 70: # İyi Durum (Açık Yeşil)
        health_status = Fore.LIGHTGREEN_EX + "İYİ" + Style.RESET_ALL
        notes = "Disk durumu İYİ. Bazı önemsiz uyarılar mevcut olabilir. Düzenli kontrol önerilir."
    elif score > 60: # Orta / Dikkat Gerektiren Durum (Açık Sarı)
        health_status = Fore.YELLOW + "ORTA" + Style.RESET_ALL
        notes = "Disk durumu ORTA. Bazı sorunlar tespit edildi. Verilerinizi yedeklemeniz ve diski gözlemlemeniz önerilir."
    else: # Kötü / Kritik Durum (Açık Kırmızı)
        health_status = Fore.LIGHTRED_EX + "KÖTÜ / KRİTİK" + Style.RESET_ALL
        notes = "Disk durumu KÖTÜ veya KRİTİK. Acil yedekleme yapın ve diski değiştirin. Veri kaybı riski çok yüksek!"

    if warnings:
        notes += "\n\nTespit Edilen Uyarılar:\n" + "\n".join([f"- {w}" for w in warnings])

    return score, health_status, notes

# --- Programın Menü ve Ana Akışı ---

def about_menu():
    """Hakkında menüsünü gösterir."""
    clear_screen() # Ekranı temizle
    print(Style.BRIGHT + "Zeus HDD Doctor Console Hakkında")
    print("============================" + Style.RESET_ALL)
    print(f"{Fore.CYAN}Sürüm:{Style.RESET_ALL} 1.0")
    print(f"{Fore.CYAN}Lisans:{Style.RESET_ALL} GNU GPLv3")
    print(f"{Fore.CYAN}Geliştiren:{Style.RESET_ALL} Zeus")
    print(f"{Fore.CYAN}Github:{Style.RESET_ALL} https://github.com/shampuan/Zeus-HDD-Doctor")
    print("\nZeus HDD Doctor, debian tabanlı sistemlerde, hafıza birimlerinin sağlık durumlarını")
    print("gösteren basit ve hafif bir yazlımdır.")
    print(Style.BRIGHT + "============================" + Style.RESET_ALL)
    input(Fore.CYAN + "Ana menüye dönmek için Enter tuşuna basın..." + Style.RESET_ALL) # Renk değiştirildi

def check_root_permissions():
    """Programın root yetkisiyle çalışıp çalışmadığını kontrol eder.
    Çalışmıyorsa kullanıcıyı uyarır ve çıkış yapar.
    """
    if os.geteuid() != 0: # geteuid() Linux'a özeldir, geçerli kullanıcı kimliğini kontrol eder
        print_header("YETKİ HATASI")
        print(Fore.RED + "Bu program root (yönetici) yetkileriyle çalıştırılmalıdır." + Style.RESET_ALL)
        print(Fore.YELLOW + "Lütfen aşağıdaki komutlardan birini kullanarak programı yeniden başlatın:" + Style.RESET_ALL)
        print(f"  {Fore.CYAN}sudo python3 {sys.argv[0]}{Style.RESET_ALL}")
        print(Fore.YELLOW + "\nVeya PolicyKit kuruluysa (daha güvenli ve grafiksel şifre istemi):" + Style.RESET_ALL)
        print(f"  {Fore.CYAN}pkexec env DISPLAY=$DISPLAY XAUTHORITY=$XAUTHORITY python3 {sys.argv[0]}{Style.RESET_ALL}")
        print_separator()
        sys.exit(1) # Yetki yoksa programdan çık

def main_menu():
    """Programın ana menüsünü gösterir ve seçenekleri yönetir."""
    while True:
        print_header("ANA MENÜ")
        print(f"1. Diskleri Analiz Et")
        print(f"2. Hakkında")
        print(f"3. Çıkış")
        print_separator()

        choice = input(Fore.YELLOW + "Seçiminizi yapın (1-3): " + Style.RESET_ALL).strip()

        if choice == '1':
            analyze_disks()
        elif choice == '2':
            about_menu()
        elif choice == '3':
            print(Fore.GREEN + "\nProgramdan çıkıldı. Hoşça kalın! Terminali kullanmaya devam edebilirsiniz." + Style.RESET_ALL)
            sys.exit(0)
        else:
            print(Fore.RED + "\nGeçersiz seçim. Lütfen tekrar deneyin." + Style.RESET_ALL)
            input(Fore.CYAN + "Devam etmek için Enter tuşuna basın..." + Style.RESET_ALL) # Renk değiştirildi

def analyze_disks():
    """Disk analiz sürecini başlatır ve raporlar."""
    print_header("DİSK ANALİZİ")
    print(Fore.CYAN + "Diskler listeleniyor ve analiz ediliyor...\n" + Style.RESET_ALL)

    disks = get_disk_list_linux()

    if not disks:
        print(Fore.RED + "\nSistemde fiziksel depolama diski bulunamadı veya listelenemedi." + Style.RESET_ALL)
        input(Fore.CYAN + "\nAna menüye dönmek için Enter tuşuna basın..." + Style.RESET_ALL) # Renk değiştirildi
        return

    disk_summary_results = [] # Özet rapor için
    detailed_disk_data = [] # Detaylı çıktı için

    for i, disk in enumerate(disks):
        print(f"\n{Fore.CYAN}--- Disk {i+1}: {disk['name']} ({disk['path']}) ---{Style.RESET_ALL}")
        print_separator()

        smart_attributes_output, smart_info_output, error_message = get_smart_data_linux(disk['path'])

        health_score = None # Başlangıçta None olarak ayarla
        health_status = ""
        notes = ""
        disk_details = {}
        smart_attributes = []
        smart_data_available = False 

        if smart_attributes_output and smart_info_output:
            parsed_disk_details = parse_smart_info(smart_info_output)
            if parsed_disk_details.get("SMART Supported") == "Enabled":
                smart_data_available = True
                disk_details = parsed_disk_details 
                smart_attributes = parse_smart_attributes(smart_attributes_output)
            else:
                smart_data_available = False
                disk_details = parsed_disk_details 
            
            # calculate_health_score, smart_data_available False ise "Bilinmiyor" stringi döndürecek
            health_score, health_status, notes = calculate_health_score(smart_attributes, disk_details, smart_data_available)

            color_code_summary = Style.RESET_ALL 
            if isinstance(health_score, str): # Eğer health_score string ise (örn: "Bilinmiyor")
                color_code_summary = Style.DIM + Fore.WHITE # Gri tonu
                score_display = health_score # % işaretini ekleme
            else: # Sayı ise
                score_display = f"%{health_score}" 
                if health_score > 85:
                    color_code_summary = Style.BRIGHT + Fore.LIGHTGREEN_EX
                elif health_score > 70:
                    color_code_summary = Fore.LIGHTGREEN_EX
                elif health_score > 60:
                    color_code_summary = Fore.YELLOW
                else: 
                    color_code_summary = Fore.LIGHTRED_EX

            disk_summary_results.append(
                f"{color_code_summary}{disk['name'].split('(')[0].strip()} ==> {score_display} {health_status}{Style.RESET_ALL}"
            )
            detailed_disk_data.append({
                'disk_info': disk,
                'disk_details': disk_details,
                'smart_attributes': smart_attributes,
                'health_score': health_score,
                'health_status': health_status,
                'notes': notes,
                'error': None
            })

            print(Fore.CYAN + "\n--- Genel Disk Bilgileri ---" + Style.RESET_ALL)
            for key, value in disk_details.items():
                print(f"  {Style.BRIGHT}{key}:{Style.RESET_ALL} {value}")

            print(Fore.CYAN + "\n--- SMART Rapor Özeti ---" + Style.RESET_ALL)
            # Burada da parantezleri kaldırdım, sadece string ise doğrudan yazdır
            display_score_report = health_score if isinstance(health_score, str) else f"%{health_score}"
            print(f"  {Style.BRIGHT}Sağlık Puanı:{Style.RESET_ALL} {display_score_report} ({health_status})")
            print(f"  {Style.BRIGHT}Notlar:{Style.RESET_ALL}\n{notes}")

        else:
            # smartctl komutu başarısız oldu veya genel bir hata var
            health_score, health_status, notes = calculate_health_score([], {}, False) # Bilinmiyor durumu için
            
            score_display = health_score # "Bilinmiyor" stringi
            color_code_summary = Style.DIM + Fore.WHITE # Gri tonu

            disk_summary_results.append(
                f"{color_code_summary}{disk['name'].split('(')[0].strip()} ==> {score_display} {health_status}{Style.RESET_ALL}"
            )
            detailed_disk_data.append({
                'disk_info': disk,
                'error': error_message,
                'health_score': health_score, 
                'health_status': health_status,
                'notes': notes
            })
            print(Fore.RED + f"\nSMART verisi alınamadı veya desteklenmiyor: {error_message}" + Style.RESET_ALL)
            print(f"  {Style.BRIGHT}Sağlık Puanı:{Style.RESET_ALL} {health_score} ({health_status})") # Parantezleri kaldırdım
            print(f"  {Style.BRIGHT}Notlar:{Style.RESET_ALL}\n{notes}")


        print_separator()
        # Renk değiştirildi
        input(Fore.CYAN + "Detaylar için Enter'a basın veya bir sonraki diske geçmek için Enter'a tekrar basın..." + Style.RESET_ALL)
        

    # Tüm disklerin özetini göster
    clear_screen()
    print_header("DİSK ANALİZİ ÖZETİ")
    for result in disk_summary_results:
        print(result)
    print_separator()

    # Kullanıcıdan detayları görmek isteyip istemediğini sor
    if detailed_disk_data:
        while True:
            # Renk değiştirildi
            choice_detail = input(Fore.CYAN + "Detaylı SMART verilerini görmek için disk numarasını girin (Örn: 1), "
                                                "Ana Menü için 'm' tuşuna basın: " + Style.RESET_ALL).strip().lower()
            if choice_detail == 'm':
                break
            try:
                disk_index = int(choice_detail) - 1
                if 0 <= disk_index < len(detailed_disk_data):
                    display_detailed_smart_attributes(detailed_disk_data[disk_index])
                else:
                    print(Fore.RED + "Geçersiz disk numarası." + Style.RESET_ALL)
            except ValueError:
                print(Fore.RED + "Geçersiz giriş. Lütfen bir sayı veya 'm' girin." + Style.RESET_ALL)
            # Renk değiştirildi
            input(Fore.CYAN + "Devam etmek için Enter'a basın..." + Style.RESET_ALL)
            clear_screen()
            print_header("DİSK ANALİZİ ÖZETİ")
            for result in disk_summary_results:
                print(result)
            print_separator()

    # Renk değiştirildi
    input(Fore.CYAN + "Ana menüye dönmek için Enter tuşuna basın..." + Style.RESET_ALL)


def display_detailed_smart_attributes(data):
    """Belirli bir diskin detaylı SMART verilerini gösterir."""
    # SMART verisine erişilemiyorsa veya hata varsa bu durumu ele al
    if isinstance(data['health_score'], str) or data['error']: # health_score string ise (yani "Bilinmiyor")
        print_header(f"DETAYLI SMART BİLGİSİ - {data['disk_info']['name']}")
        print(f"{Fore.CYAN}Genel Bilgiler:{Style.RESET_ALL}")
        # Disk bilgileri varsa yazdır
        if 'disk_details' in data and data['disk_details']:
            for key, value in data['disk_details'].items():
                print(f"  {Style.BRIGHT}{key}:{Style.RESET_ALL} {value}")
        else:
             print(f"  {Style.DIM + Fore.WHITE}Disk bilgileri sınırlı veya mevcut değil.{Style.RESET_ALL}")

        print(f"\n{Fore.CYAN}SMART Sağlık Durumu:{Style.RESET_ALL}")
        # Burada da parantezleri kaldırdım
        print(f"  {Style.BRIGHT}Puan:{Style.RESET_ALL} {data['health_score']} ({data['health_status']})") 
        print(f"  {Style.BRIGHT}Notlar:{Style.RESET_ALL}\n{data['notes']}")
        print(f"\n{Style.DIM + Fore.WHITE}Aygıtın SMART verilerine erişilemediği için detaylı öznitelik tablosu mevcut değildir.{Style.RESET_ALL}")
        print_separator()
        # Renk değiştirildi
        input(Fore.CYAN + "Ana menüye dönmek için Enter'a basın..." + Style.RESET_ALL)
        return

    # SMART verisi mevcutsa detaylı tabloyu göster
    print_header(f"DETAYLI SMART BİLGİSİ - {data['disk_info']['name']}")
    print(f"{Fore.CYAN}Genel Bilgiler:{Style.RESET_ALL}")
    for key, value in data['disk_details'].items():
        print(f"  {Style.BRIGHT}{key}:{Style.RESET_ALL} {value}")

    print(f"\n{Fore.CYAN}SMART Sağlık Durumu:{Style.RESET_ALL}")
    print(f"  {Style.BRIGHT}Puan:{Style.RESET_ALL} %{data['health_score']} ({data['health_status']})")
    print(f"  {Style.BRIGHT}Notlar:{Style.RESET_ALL}\n{data['notes']}")

    print(Fore.CYAN + "\n--- Detaylı SMART Verileri ---" + Style.RESET_ALL)
    print(f"{Style.BRIGHT}{'ID':<4} {'Name':<25} {'Cur':<6} {'Wor':<6} {'Thr':<6} {'Type':<12} {'Raw Value':<12}{Style.RESET_ALL}")
    print("-" * 80)
    for attr in data['smart_attributes']:
        color = Style.RESET_ALL
        # Renklendirme mantığı (Derecelendirme ile uyumlu)
        critical_raw_value_attributes_ids = {1, 5, 7, 196, 197, 198, 199}
        if attr["Threshold"] > 0 and attr["Current"] < attr["Threshold"]:
            color = Fore.LIGHTRED_EX # Eşik altında ise açık kırmızı
        elif attr["ID"] in critical_raw_value_attributes_ids and attr["Raw_Value"] > 0:
            color = Fore.YELLOW # Kritik raw değeri varsa sarı
        elif (attr["ID"] == 194 or "Temperature" in attr["Name"]) and (attr["Raw_Value"] if attr["ID"] == 194 else attr["Current"]) > 50:
             color = Fore.YELLOW # Sıcaklık yüksekse sarı

        print(f"{color}{attr['ID']:<4} {attr['Name']:<25} {attr['Current']:<6} {attr['Worst']:<6} {attr['Threshold']:<6} {attr['Type']:<12} {attr['Raw_Value']:<12}{Style.RESET_ALL}")
    print_separator()
    input(Fore.CYAN + "Ana menüye dönmek için Enter'a basın..." + Style.RESET_ALL)


# --- Program Başlangıcı ---
if __name__ == "__main__":
    check_root_permissions() # Program başlarken root yetkisi kontrolü
    main_menu() # Ana menüyü başlat
