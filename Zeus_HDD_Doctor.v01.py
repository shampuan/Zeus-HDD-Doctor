import sys
import subprocess
import os
import re
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QTextEdit, QListWidget, QListWidgetItem, QSizePolicy, QMessageBox,
    QDialog
)
from PyQt5.QtGui import QColor, QFont, QPixmap
from PyQt5.QtCore import Qt, QTimer, QSize, QProcess 

# smartctl ve disk bilgileri ile ilgili fonksiyonlar
def get_disk_list():
    """
    Sistemdeki diskleri listeler.
    """
    try:
        output = subprocess.check_output(['lsblk', '-o', 'NAME,SIZE,TYPE,MODEL,VENDOR', '-n']).decode('utf-8')
        disks = []
        for line in output.splitlines():
            parts = line.strip().split()
            if len(parts) >= 3 and parts[2] == "disk":
                disk_name = parts[0]
                disk_size = parts[1]
                model_vendor_parts = parts[3:]
                full_model_vendor = " ".join(model_vendor_parts).strip() 

                full_name = f"{disk_name} ({disk_size}) - {full_model_vendor}".strip()

                disks.append({'path': f"/dev/{disk_name}", 'name': full_name})
        return disks
    except FileNotFoundError:
        QMessageBox.critical(None, "Hata", "lsblk komutu bulunamadı. Lütfen yüklü olduğundan emin olun.")
        return []
    except subprocess.CalledProcessError as e:
        error_detail = e.stderr.decode('utf-8').strip() if e.stderr else "Detay yok."
        QMessageBox.critical(None, "Hata", f"lsblk komutu çalıştırılırken sorun oluştu: {error_detail}")
        return []

def get_smart_data(disk_path):
    """
    Belirtilen diskin SMART verilerini smartctl komutu ile alır.
    Program zaten root yetkisiyle çalışacağı için 'sudo' veya 'pkexec' kullanmaya gerek yok.
    """
    attributes_output = None
    info_output = None
    error_message = ""

    # Denenecek aygıt tipleri listesi
    device_types = ['sat', 'nvme', 'usb', 'usbjm', 'usbscsi', 'jmicron', 'scsi', 'ata']

    for dev_type in device_types:
        try:
            attributes_output = subprocess.check_output(['smartctl', '-A', '-d', dev_type, disk_path], stderr=subprocess.PIPE, timeout=20).decode('utf-8')
            info_output = subprocess.check_output(['smartctl', '-i', '-d', dev_type, disk_path], stderr=subprocess.PIPE, timeout=20).decode('utf-8')

            if "SMART support is: Disabled" in info_output or "SMART Disabled" in info_output:
                error_message = f"Disk '{disk_path}' SMART özelliğini desteklemiyor veya devre dışı."
                attributes_output = None
                info_output = None
                return None, None, error_message

            return attributes_output, info_output, "" # Hata yok
        except subprocess.CalledProcessError as e:
            error_detail = e.stderr.decode('utf-8').strip() if e.stderr else "Detay yok."
            error_message = f"smartctl '{dev_type}' tipiyle '{disk_path}' için çalıştırılamadı. Hata: {error_detail}"
            attributes_output = None
            info_output = None
        except FileNotFoundError:
            error_message = "smartctl komutu bulunamadı. Lütfen smartmontools yüklü olduğundan emin olun."
            return None, None, error_message
        except subprocess.TimeoutExpired:
            error_message = f"smartctl '{dev_type}' tipiyle '{disk_path}' için zaman aşımına uğradı."
            return None, None, error_message
        except Exception as e:
            error_message = f"Bilinmeyen bir hata oluştu: {e}"
            return None, None, error_message

    return None, None, error_message if error_message else f"Disk '{disk_path}' için SMART verileri alınamadı veya desteklenmiyor."


def parse_smart_attributes(smart_attributes_output):
    """
    smartctl -A çıktısını ayrıştırarak SMART özniteliklerini bir sözlük listesi olarak döndürür.
    Genişletilmiş regex ile tüm olası attribute satırlarını yakalamaya çalışır.
    """
    attributes = []

    attribute_pattern = re.compile(
        r'^\s*(\d+)\s+([a-zA-Z0-9_]+)\s+'     # 1: ID, 2: Name
        r'(\S+)\s+'                         # 3: Flags (örn: 0x000f, or '---')
        r'(\d+)\s+'                         # 4: Current Value
        r'(\d+)\s+'                         # 5: Worst Value
        r'(\d+)\s+'                         # 6: Threshold Value
        r'(\S+)\s+'                         # 7: Type (Pre-fail, Old_age)
        r'(\S+)\s+'                         # 8: Updated (Always, Offline)
        r'(\S+)\s+'                         # 9: When_Failed (-, In_the_past)
        r'([-]?\d+)$'                       # 10: Raw_Value (integer, possibly negative, at end of line)
    )

    start_parsing = False
    for line in smart_attributes_output.splitlines():
        if "ID# ATTRIBUTE_NAME" in line:
            start_parsing = True
            continue
        if start_parsing:
            if line.strip() == "" or "SMART Error Log" in line or "SMART Self-test Log" in line:
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
                except ValueError as ve:
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
                    except Exception as e:
                        pass
            else:
                pass
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
            match = re.search(r'\[(.*?)\]', line)
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
        elif "Wear_Leveling_Count" in line:
             match = re.match(r'.*Wear_Leveling_Count\s+.*?\s+(\d+)', line)
             if match:
                 info["Wear Leveling"] = match.group(1)
        elif "Media_Wearout_Indicator" in line:
             match = re.match(r'.*Media_Wearout_Indicator\s+.*?\s+(\d+)', line)
             if match:
                 info["Media Wearout"] = match.group(1)
        elif "Data Units Written:" in line:
            info["Data Units Written"] = line.split(":", 1)[1].strip()
        elif "Data Units Read:" in line:
            info["Data Units Read"] = line.split(":", 1)[1].strip()

    return info

def calculate_health_score(attributes, disk_info):
    """
    SMART özniteliklerine göre basit bir sağlık puanı hesaplar (0-100).
    """
    score = 100
    critical_raw_value_attributes_ids = {
        1, 5, 7, 196, 197, 198, 199
    }

    warnings = []

    for attr in attributes:
        if attr["Threshold"] > 0 and attr["Current"] < attr["Threshold"]:
            score -= 15
            warnings.append(f"'{attr['Name']}' (ID:{attr['ID']}) kritik eşik ({attr['Threshold']}) altında ({attr['Current']})!")

        if attr["ID"] in critical_raw_value_attributes_ids and attr["Raw_Value"] > 0:
            score -= 10
            warnings.append(f"'{attr['Name']}' (ID:{attr['ID']}) Raw Value'u 0'dan büyük ({attr['Raw_Value']})!")

        if attr["ID"] == 194 or "Temperature" in attr["Name"]:
            current_temp = attr["Raw_Value"] if attr["ID"] == 194 else attr["Current"]
            if current_temp > 50:
                score -= 5
                warnings.append(f"Disk sıcaklığı yüksek ({current_temp}°C).")
            elif current_temp > 60:
                score -= 15
                warnings.append(f"DİKKAT: Disk sıcaklığı çok yüksek ({current_temp}°C)!")

        if attr["ID"] == 177 and attr["Raw_Value"] > 0:
            if attr["Raw_Value"] > 50000:
                score -= 5
                warnings.append(f"SSD yıpranma düzeyi yüksek: {attr['Raw_Value']} (Wear_Leveling_Count).")
        elif attr["ID"] == 233 and attr["Raw_Value"] < 100:
            if attr["Raw_Value"] < 20:
                score -= 20
                warnings.append(f"SSD yıpranma düzeyi kritik: %{attr['Raw_Value']} (Media_Wearout_Indicator).")
            elif attr["Raw_Value"] < 50:
                score -= 10
                warnings.append(f"SSD yıpranma düzeyi yüksek: %{attr['Raw_Value']} (Media_Wearout_Indicator).")


    score = max(0, min(100, score))

    health_status = ""
    notes = ""
    if score >= 85:
        health_status = "MÜKEMMEL"
        notes = "Disk durumu MÜKEMMEL. Herhangi bir işlem gerekli değildir."
    elif score >= 70:
        health_status = "İYİ"
        notes = "Disk durumu İYİ. Bazı önemsiz uyarılar mevcut olabilir. Düzenli kontrol önerilir."
    elif score >= 60:
        health_status = "ORTA"
        notes = "Disk durumu ORTA. Bazı sorunlar tespit edildi. Verilerinizi yedeklemenizi ve diski gözlemlemeniz önerilir."
    else:
        health_status = "KÖTÜ / KRİTİK"
        notes = "Disk durumu KÖTÜ veya KRİTİK. Acil yedekleme yapın ve diski değiştirin. Veri kaybı riski çok yüksek!"

    if warnings:
        notes += "\n\nTespit Edilen Uyarılar:\n" + "\n".join([f"- {w}" for w in warnings])

    return score, health_status, notes

# Hakkında penceresi sınıfı
class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Zeus HDD Doctor Hakkında")
        self.setFixedSize(350, 200) # Sabit boyut
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignCenter) # Ortala

        title_label = QLabel("Zeus HDD Doctor v1.0.1")
        title_label.setFont(QFont("Arial", 16, QFont.Bold))
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)

        version_label = QLabel("Sürüm: 1.0.1") # Sürüm bilgisi
        version_label.setFont(QFont("Arial", 10))
        version_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(version_label)

        author_label = QLabel("Author: @Zeus https://github.com/shampuan/") # Yazar bilgisi
        author_label.setFont(QFont("Arial", 10))
        author_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(author_label)

        description_label = QLabel("Linux disk sağlığını takip etmek için mükemmel bir linux aracı.")
        description_label.setFont(QFont("Arial", 9))
        description_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(description_label)

        # OK butonu ekleyelim
        ok_button = QPushButton("Tamam")
        ok_button.clicked.connect(self.accept) # Pencereyi kapatır
        layout.addWidget(ok_button, alignment=Qt.AlignCenter)

        self.setLayout(layout)


class ZeusHDDDoctor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Zeus HDD Doctor v1.0.1")
        self.setGeometry(100, 100, 1100, 750)

        # QProcess nesnesi oluşturuluyor
        self.shred_process = QProcess(self)
        # Sinyal ve slot bağlantıları
        self.shred_process.readyReadStandardError.connect(self.update_shred_progress)
        self.shred_process.finished.connect(self.shred_finished)
        self.shred_process.errorOccurred.connect(self.shred_error_occurred) # Hata sinyali bağlandı

        # Stderr buffer'ı başlat
        self.stderr_buffer = ""

        self.init_ui()
        self.load_disks()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        # Sol panel (Disk Listesi + Zeus Amblemi)
        left_panel = QVBoxLayout()
        self.disk_list_label = QLabel("Diskler:")
        self.disk_list_label.setFont(QFont("Arial", 12, QFont.Bold))
        left_panel.addWidget(self.disk_list_label)

        self.disk_list_widget = QListWidget()
        self.disk_list_widget.setFixedWidth(300)
        # QListWidget'ı dikeyde genişletmek için stretch faktörü ile ekleyin
        left_panel.addWidget(self.disk_list_widget, 1) # <--- Burası değiştirildi
        self.disk_list_widget.itemClicked.connect(self.on_disk_selected)

        # Zeus Amblemi
        self.zeus_logo_label = QLabel()
        pixmap = QPixmap("/usr/share/icons/zeus1.png")
        if not pixmap.isNull():
            self.zeus_logo_label.setPixmap(pixmap)
            self.zeus_logo_label.setScaledContents(True) # <--- Otomatik boyutlandırma etkinleştirildi
            self.zeus_logo_label.setAlignment(Qt.AlignCenter)
            # Logoyu alttaki yerine ekleyin, disk listesi yukarıda boşluğu doldurur
            left_panel.addWidget(self.zeus_logo_label) # <--- Burası değiştirildi
        else:
            print("Uyarı: zeus1.png bulunamadı veya yüklenemedi. Lütfen /usr/share/icons/zeus1.png yolunu kontrol edin.")
            self.zeus_logo_label.setText("Amblem Yüklenemedi")
            self.zeus_logo_label.setAlignment(Qt.AlignCenter)
            left_panel.addWidget(self.zeus_logo_label)

        # left_panel.addStretch(1) # Bu satıra artık gerek yok, QListWidget boşluğu dolduracak

        main_layout.addLayout(left_panel)

        # Sağ panel (Detaylar)
        right_panel = QVBoxLayout()

        # Genel Bilgiler Alanı
        self.general_info_label = QLabel("Seçili Disk Bilgileri:")
        self.general_info_label.setFont(QFont("Arial", 12, QFont.Bold))
        right_panel.addWidget(self.general_info_label)

        self.disk_details_text = QTextEdit()
        self.disk_details_text.setReadOnly(True)
        self.disk_details_text.setFixedHeight(150)
        self.disk_details_text.setFont(QFont("Monospace", 10))
        right_panel.addWidget(self.disk_details_text)

        # Durum ve Puanlama Alanı (Hard Disk Sentinel benzeri)
        self.health_status_label = QLabel("Sağlık: N/A")
        self.health_status_label.setFont(QFont("Arial", 16, QFont.Bold))
        self.health_status_label.setStyleSheet("background-color: lightgray; padding: 10px; border-radius: 5px;")
        self.health_status_label.setAlignment(Qt.AlignCenter)
        right_panel.addWidget(self.health_status_label)

        self.notes_text = QTextEdit()
        self.notes_text.setReadOnly(True)
        self.notes_text.setFont(QFont("Arial", 10))
        self.notes_text.setFixedHeight(200)
        self.notes_text.setStyleSheet("background-color: #e0ffe0; border: 1px solid #c0e0c0; padding: 5px; border-radius: 5px;")
        right_panel.addWidget(self.notes_text)

        # Diski Satışa Hazırla Bölümü
        self.secure_erase_label = QLabel("Diski satışa hazırla: (ÇOK UZUN SÜRER)")
        self.secure_erase_label.setFont(QFont("Arial", 12, QFont.Bold))
        right_panel.addWidget(self.secure_erase_label)

        # Yüzde göstergesi için yeni QLabel
        self.progress_label = QLabel("İşlem sürüyor: --%")
        self.progress_label.setFont(QFont("Arial", 11))
        self.progress_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter) # Sola hizala
        self.progress_label.setMinimumWidth(150) # Yeterli alan sağlayın

        self.secure_erase_button = QPushButton("Güvenli Sil")
        self.secure_erase_button.setFixedSize(120, 30)
        self.secure_erase_button.clicked.connect(self.initiate_secure_erase)
        
        # Buton ve ilerleme etiketi için düzenleme
        secure_erase_button_layout = QHBoxLayout()
        secure_erase_button_layout.addWidget(self.secure_erase_button) # Butonu sola taşı
        secure_erase_button_layout.addWidget(self.progress_label) # Yüzdeyi yanına ekle
        secure_erase_button_layout.addStretch(1) # Boşluğu sağa iter
        right_panel.addLayout(secure_erase_button_layout)


        # Attributes Tablosu
        self.attributes_label = QLabel("SMART Raporları:")
        self.attributes_label.setFont(QFont("Arial", 12, QFont.Bold))
        right_panel.addWidget(self.attributes_label)

        self.attributes_table = QTableWidget()
        self.attributes_table.setColumnCount(7)
        self.attributes_table.setHorizontalHeaderLabels(["ID", "Name", "Current", "Worst", "Threshold", "Type", "Raw Value"])
        self.attributes_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.attributes_table.setEditTriggers(QTableWidget.NoEditTriggers)
        right_panel.addWidget(self.attributes_table)

        # Butonlar için yatay layout (Hakkında ve Yenile)
        button_layout = QHBoxLayout()
        button_layout.addStretch(1)

        # Hakkında butonu
        self.about_button = QPushButton("Hakkında")
        self.about_button.clicked.connect(self.show_about_dialog)
        self.about_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        button_layout.addWidget(self.about_button)

        # Yenile düğmesi
        self.refresh_button = QPushButton("Seçili Diski Yenile")
        self.refresh_button.clicked.connect(self.refresh_selected_disk)
        self.refresh_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        button_layout.addWidget(self.refresh_button)

        right_panel.addLayout(button_layout)

        main_layout.addLayout(right_panel, 1)

    def load_disks(self):
        self.disk_list_widget.clear()
        self.disks = get_disk_list()
        if not self.disks:
            QMessageBox.warning(self, "Disk Bulunamadı", "Sistemde depolama diski bulunamadı veya listelenemedi.")
            return

        for disk in self.disks:
            item = QListWidgetItem(disk['name'])
            item.setData(Qt.UserRole, disk['path'])
            self.disk_list_widget.addItem(item)

        if self.disks:
            self.disk_list_widget.setCurrentRow(0)
            self.on_disk_selected(self.disk_list_widget.currentItem())

    def on_disk_selected(self, item):
        if item:
            selected_disk_path = item.data(Qt.UserRole)
            self.display_disk_data(selected_disk_path)
        else:
            self.clear_display()

    def refresh_selected_disk(self):
        current_item = self.disk_list_widget.currentItem()
        if current_item:
            selected_disk_path = current_item.data(Qt.UserRole)
            self.display_disk_data(selected_disk_path)
        else:
            QMessageBox.information(self, "Yenile", "Lütfen yenilemek için bir disk seçin.")
            self.clear_display()

    def show_about_dialog(self):
        """Hakkında penceresini açar."""
        about_dialog = AboutDialog(self)
        about_dialog.exec_()

    def initiate_secure_erase(self):
        selected_item = self.disk_list_widget.currentItem()
        if not selected_item:
            QMessageBox.warning(self, "Güvenli Silme Uyarısı", "Lütfen güvenli silme işlemi yapmak için bir disk seçin.")
            return

        selected_disk_name = selected_item.text()
        self.selected_disk_path = selected_item.data(Qt.UserRole) # Diski sınıf değişkenine kaydet
        
        # İşlem zaten devam ediyorsa yeni bir işlem başlatma
        if self.shred_process.state() == QProcess.Running:
            QMessageBox.warning(self, "İşlem Zaten Devam Ediyor", "Zaten bir güvenli silme işlemi devam ediyor. Lütfen bitmesini bekleyin.")
            return

        # İlk onay kutusu
        reply = QMessageBox.warning(self, "TEHLİKELİ İŞLEM ONAYI",
                                     f"SEÇİLİ DİSK: {selected_disk_name} ({self.selected_disk_path})\n\n"
                                     "Bu işlem, seçilen diskteki TÜM VERİLERİ GERİ DÖNÜLMEZ BİR ŞEKİLDE SİLECEKTİR!\n"
                                     "Silinecek diskten eminseniz 'Evet'i tıklayın. Emin değilseniz 'Hayır'ı tıklayın.\n\n"
                                     "DEVAM ETMEK İSTİYOR MUSUNUZ?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

        if reply == QMessageBox.No:
            QMessageBox.information(self, "Güvenli Silme İptal Edildi", "Güvenli silme işlemi iptal edildi.")
            return

        # İkinci onay kutusu (son bir uyarı)
        final_reply = QMessageBox.critical(self, "SON UYARI: VERİ KAYBI RİSKİ!",
                                            f"GERİ DÖNÜŞ YOK! {selected_disk_name} ({self.selected_disk_path}) diskindeki TÜM VERİLER SİLİNECEK.\n"
                                            "Bu işlemi gerçekten onaylıyor musunuz?\n\n"
                                            "Bu işlem geri alınamaz!",
                                            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

        if final_reply == QMessageBox.Yes:
            # Shred komutunu QProcess ile başlat
            QMessageBox.information(self, "İşlem Başlatıldı",
                                    f"'{selected_disk_name}' diski için güvenli silme işlemi başlatıldı.\n"
                                    "İlerleme yüzdesi ana pencerede gösterilecektir.\n"
                                    "Lütfen işlem bitene kadar programı kapatmayın veya diski çıkarmayın.")

            self.secure_erase_button.setEnabled(False) # Butonu devre dışı bırak
            self.progress_label.setText("İşlem sürüyor: 0%") # İlerleme etiketini sıfırla
            self.stderr_buffer = "" # Arabelleği sıfırla

            program = 'shred'
            # -n 0 parametresi ile rastgele geçiş yapılmamasını, sadece -z (sıfır) geçişinin yapılmasını sağlıyoruz
            arguments = ['-v', '-n', '0', '-z', self.selected_disk_path] 
            
            self.shred_process.start(program, arguments)
            
        else:
            QMessageBox.information(self, "Güvenli Silme İptal Edildi", "Güvenli silme işlemi iptal edildi.")

    def update_shred_progress(self):
        """
        shred komutunun stderr çıktısını okur ve ilerleme yüzdesini günceller.
        """
        self.stderr_buffer += self.shred_process.readAllStandardError().data().decode(errors='ignore')
        
        last_percentage = None
        # Arabellekten tam satırları ayıklayın ve kalan kısmi satırı new_buffer'a aktarın
        lines = self.stderr_buffer.split('\n')
        # Son satır tamamlanmamışsa, onu new_buffer'a kaydet ve işlenecek satırlardan çıkar
        if lines and not self.stderr_buffer.endswith('\n'):
            new_buffer = lines[-1]
            lines = lines[:-1]
        else:
            new_buffer = "" # Tüm satırlar tamamlanmışsa arabelleği sıfırla

        for line in lines:
            # --- DEBUG: Print raw output from shred ---
            print(f"SHRED_OUTPUT: {line.strip()}") 
            # ----------------------------------------

            # Yüzde bilgisini içeren satırı ara - Düzeltilmiş regex
            match = re.search(r'(\d+)%', line) 
            if match:
                last_percentage = match.group(1)
            # "Başlıyor..." mesajı için, eğer henüz bir yüzde gösterilmiyorsa
            elif "Pass 1/1 (zero)..." in line and not re.search(r'\d+%', self.progress_label.text()):
                self.progress_label.setText("İşlem sürüyor: Başlıyor...")

        self.stderr_buffer = new_buffer # Kalan kısmi satırı bir sonraki okuma için sakla

        if last_percentage is not None:
            self.progress_label.setText(f"İşlem sürüyor: %{last_percentage}")
            QApplication.processEvents() # GUI'nin güncellenmesini ve olayları işlemesini sağla

    def shred_finished(self, exit_code, exit_status):
        """
        shred işlemi tamamlandığında veya hata oluştuğunda çağrılır.
        """
        self.secure_erase_button.setEnabled(True) # Butonu tekrar etkinleştir
        self.progress_label.setText("İşlem bitti: --%") # İlerleme etiketini sıfırla
        self.stderr_buffer = "" # Arabelleği temizle

        if exit_code == 0: # Başarılı tamamlandı
            QMessageBox.information(self, "İşlem Tamamlandı",
                                    f"'{self.selected_disk_path}' diski güvenli bir şekilde silindi.")
        else: # Hata oluştu
            error_output = self.shred_process.readAllStandardError().data().decode(errors='ignore') # Kalan hataları oku
            QMessageBox.critical(self, "Güvenli Silme Hatası",
                                 f"'{self.selected_disk_path}' diski silinirken hata oluştu.\n"
                                 f"Çıkış kodu: {exit_code}\n"
                                 f"Detay: {error_output if error_output.strip() else 'Detay yok.'}")
        # Disk bilgilerini tekrar yükle (işlem sonrası durumu görmek için)
        self.display_disk_data(self.selected_disk_path)

    def shred_error_occurred(self, error):
        """
        QProcess bir hata ile karşılaştığında çağrılır (örn: 'shred' komutu bulunamadı).
        """
        self.secure_erase_button.setEnabled(True)
        self.progress_label.setText("Hata: --%")
        self.stderr_buffer = "" # Arabelleği temizle
        error_message = ""
        if error == QProcess.FailedToStart:
            error_message = "Komut başlatılamadı. 'shred' kurulu olmayabilir veya PATH'inizde bulunmuyor olabilir. coreutils paketini kontrol edin."
        elif error == QProcess.Crashed:
            error_message = "Komut beklenmedik şekilde çöktü."
        elif error == QProcess.TimedOut:
            error_message = "Komut zaman aşımına uğradı."
        else:
            error_message = f"Bilinmeyen bir QProcess hatası oluştu: {error}"
        
        QMessageBox.critical(self, "Komut Çalıştırma Hatası", error_message)


    def display_disk_data(self, disk_path):
        self.clear_display()

        self.disk_details_text.setText(f"'{disk_path}' diski için bilgiler yükleniyor...")
        self.health_status_label.setText("Sağlık: Yükleniyor...")
        self.notes_text.setText("Veriler alınıyor...")
        self.health_status_label.setStyleSheet("background-color: lightgray; padding: 10px; border-radius: 5px;")
        self.notes_text.setStyleSheet("background-color: #e0ffe0; border: 1px solid #c0e0c0; padding: 5px; border-radius: 5px;")

        attributes_output, info_output, error_message = get_smart_data(disk_path)

        if attributes_output and info_output:
            smart_attributes = parse_smart_attributes(attributes_output)
            disk_info = parse_smart_info(info_output)

            info_text = f"Device Model: {disk_info.get('Device Model', 'N/A')}\n" \
                        f"Serial Number: {disk_info.get('Serial Number', 'N/A')}\n" \
                        f"Firmware: {disk_info.get('Firmware Version', 'N/A')}\n" \
                        f"Capacity: {disk_info.get('User Capacity', 'N/A')}\n" \
                        f"Rotation Rate: {disk_info.get('Rotation Rate', 'N/A')}\n" \
                        f"Power On: {disk_info.get('Power On Hours', 'N/A')}\n" \
                        f"Power Cycles: {disk_info.get('Power Cycle Count', 'N/A')}\n" \
                        f"SMART Supported: {disk_info.get('SMART Supported', 'N/A')}"

            if "Data Units Written" in disk_info:
                info_text += f"\nData Units Written: {disk_info.get('Data Units Written', 'N/A')}"
            if "Data Units Read" in disk_info:
                info_text += f"\nData Units Read: {disk_info.get('Data Units Read', 'N/A')}"

            self.disk_details_text.setText(info_text)

            if smart_attributes:
                health_score, health_status, notes = calculate_health_score(smart_attributes, disk_info)
                self.health_status_label.setText(f"Sağlık: %{health_score} ({health_status})")
                self.notes_text.setText(notes)

                # Renkleri ve arkaplanları 'disk durumu.png' resmine göre ayarlama
                if health_score >= 85:
                    self.health_status_label.setStyleSheet("background-color: #8FD8A0; padding: 10px; border-radius: 5px;")
                    self.notes_text.setStyleSheet("background-color: #e0ffe0; border: 1px solid #c0e0c0; padding: 5px; border-radius: 5px;")
                elif health_score >= 70:
                    self.health_status_label.setStyleSheet("background-color: #C1E070; padding: 10px; border-radius: 5px;")
                    self.notes_text.setStyleSheet("background-color: #f0ffe0; border: 1px solid #e0e0c0; padding: 5px; border-radius: 5px;")
                elif health_score >= 60:
                    self.health_status_label.setStyleSheet("background-color: #FFD252; padding: 10px; border-radius: 5px;")
                    self.notes_text.setStyleSheet("background-color: #ffe0c0; border: 1px solid #e0c0a0; padding: 5px; border-radius: 5px;")
                else:
                    self.health_status_label.setStyleSheet("background-color: #E27272; padding: 10px; border-radius: 5px;")
                    self.notes_text.setStyleSheet("background-color: #ffe0e0; border: 1px solid #c0c0c0; padding: 5px; border-radius: 5px;")


                self.attributes_table.setRowCount(len(smart_attributes))
                for row, attr in enumerate(smart_attributes):
                    self.attributes_table.setItem(row, 0, QTableWidgetItem(str(attr['ID'])))
                    self.attributes_table.setItem(row, 1, QTableWidgetItem(attr['Name']))
                    self.attributes_table.setItem(row, 2, QTableWidgetItem(str(attr['Current'])))
                    self.attributes_table.setItem(row, 3, QTableWidgetItem(str(attr['Worst'])))
                    self.attributes_table.setItem(row, 4, QTableWidgetItem(str(attr['Threshold'])))
                    self.attributes_table.setItem(row, 5, QTableWidgetItem(attr['Type']))
                    self.attributes_table.setItem(row, 6, QTableWidgetItem(str(attr['Raw_Value'])))
            else:
                self.health_status_label.setText("Sağlık: Bilgi Yok (Ayrıştırılamadı)")
                self.health_status_label.setStyleSheet("background-color: lightblue; padding: 10px; border-radius: 5px;")
                self.notes_text.setText(f"'{disk_path}' için SMART öznitelikleri ayrıştırılamadı. SMART desteklemiyor olabilir veya veri formatı GSmartControl'den farklı olabilir.\n"
                                        f"Detay: {error_message if error_message else 'Bilinmiyor'}")
        else:
            self.health_status_label.setText("Sağlık: HATA / Desteklenmiyor")
            self.health_status_label.setStyleSheet("background-color: #E0666C; padding: 10px; border-radius: 5px;")
            self.notes_text.setText(f"Disk '{disk_path}' için SMART verileri alınamadı.\n"
                                     f"Muhtemel Nedenler:\n"
                                     f"- Disk SMART özelliğini desteklemiyor.\n"
                                     f"- smartmontools yüklü değil.\n"
                                     f"- Yetkilendirme reddedildi (parolayı yanlış girmiş olabilirsiniz veya uygulama root yetkisiyle başlatılamadı).\n"
                                     f"- USB adaptörü veya denetleyici smartctl tarafından tanınmıyor.\n\n"
                                     f"Detay: {error_message}")
            self.notes_text.setStyleSheet("background-color: #ffe0e0; border: 1px solid #c0c0c0; padding: 5px; border-radius: 5px;")


    def clear_display(self):
        self.disk_details_text.clear()
        self.health_status_label.setText("Sağlık: N/A")
        self.health_status_label.setStyleSheet("background-color: lightgray; padding: 10px; border-radius: 5px;")
        self.notes_text.clear()
        self.notes_text.setStyleSheet("background-color: #e0ffe0; border: 1px solid #c0e0c0; padding: 5px; border-radius: 5px;")
        self.attributes_table.setRowCount(0)

if __name__ == "__main__":
    if os.geteuid() != 0:
        script_path = os.path.abspath(sys.argv[0])
        display_var = os.environ.get('DISPLAY')
        xauthority_var = os.environ.get('XAUTHORITY')

        if not display_var:
            QMessageBox.critical(None, "Hata", "DISPLAY ortam değişkeni bulunamadı. Programı grafik bir ortamda (masaüstü) çalıştırmanız gerekmektedir.")
            sys.exit(1)

        try:
            command = ['pkexec', 'env',
                       f'DISPLAY={display_var}',
                       f'XAUTHORITY={xauthority_var}',
                       sys.executable, script_path] + sys.argv[1:]

            subprocess.run(command, check=True)
            sys.exit(0)
        except FileNotFoundError:
            QMessageBox.critical(None, "Hata", "pkexec veya Python interpreter bulunamadı. Lütfen 'policykit-1' ve 'python3' paketlerinin yüklü olduğundan emin olun.")
            sys.exit(1)
        except subprocess.CalledProcessError as e:
            error_detail = e.stderr.decode('utf-8').strip() if e.stderr else "Detay yok."
            QMessageBox.critical(None, "Yetkilendirme Hatası",
                                 f"Programı root yetkisiyle başlatırken hata oluştu veya yetkilendirme reddedildi.\\n"
                                 f"Detay: {error_detail}")
            sys.exit(1)
        except Exception as e:
            QMessageBox.critical(None, "Hata", f"Programı yeniden başlatırken bilinmeyen bir hata oluştu: {e}")
            sys.exit(1)

    app = QApplication(sys.argv)
    window = ZeusHDDDoctor()
    window.show()
    sys.exit(app.exec_())
