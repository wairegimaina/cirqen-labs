# PDF Generator Configuration
# Configuration file for customizing calibration certificate PDFs

from reportlab.lib.colors import HexColor
from reportlab.lib.units import inch, mm, cm
from reportlab.lib.pagesizes import A4, letter
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

class PDFConfiguration:
    """
    Configuration class for PDF certificate generation.
    Allows customization of layout, styling, and content.
    """
    
    # Page Layout Settings
    PAGE_SIZE = A4
    MARGINS = {
        'left': 40,
        'right': 40,
        'top': 60,
        'bottom': 60
    }
    
    # Color Scheme
    COLORS = {
        'primary': HexColor('#1e40af'),      # Blue
        'secondary': HexColor('#374151'),     # Dark gray
        'success': HexColor('#059669'),       # Green
        'error': HexColor('#dc2626'),         # Red
        'warning': HexColor('#f59e0b'),       # Yellow
        'background': HexColor('#f9fafb'),    # Light gray
        'border': HexColor('#d1d5db'),        # Gray border
        'text': HexColor('#1f2937'),          # Dark text
        'light_text': HexColor('#6b7280'),    # Light text
        'white': HexColor('#ffffff'),
        'black': HexColor('#000000')
    }
    
    # Typography Settings
    FONTS = {
        'title': {
            'family': 'Helvetica-Bold',
            'size': 24,
            'color': COLORS['primary']
        },
        'section_header': {
            'family': 'Helvetica-Bold',
            'size': 12,
            'color': COLORS['text']
        },
        'normal': {
            'family': 'Helvetica',
            'size': 10,
            'color': COLORS['text']
        },
        'small': {
            'family': 'Helvetica',
            'size': 8,
            'color': COLORS['light_text']
        },
        'footer': {
            'family': 'Helvetica',
            'size': 8,
            'color': COLORS['light_text']
        }
    }
    
    # Table Styling
    TABLE_STYLES = {
        'header': {
            'background': COLORS['primary'],
            'text_color': COLORS['white'],
            'font_family': 'Helvetica-Bold',
            'font_size': 10
        },
        'data': {
            'font_family': 'Helvetica',
            'font_size': 9,
            'row_colors': [COLORS['white'], HexColor('#f8fafc')]
        },
        'pass_fail': {
            'pass_bg': HexColor('#d1fae5'),
            'pass_text': COLORS['success'],
            'fail_bg': HexColor('#fee2e2'),
            'fail_text': COLORS['error']
        }
    }
    
    # Chart Settings
    CHART_CONFIG = {
        'width': 6.5 * inch,
        'height': 4 * inch,
        'dpi': 300,
        'colors': ['#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6'],
        'grid_alpha': 0.3,
        'title_size': 12,
        'axis_label_size': 10
    }
    
    # Laboratory Information
    LABORATORY_INFO = {
        'name': 'KNH CALIBRATION LABORATORY',
        'Department': 'Biomedical Engineering Department',
        'organization': 'Kenyatta National Hospital',
        # Path to the organisation logo.
        # Accepts an absolute path OR a path relative to STATIC_ROOT / BASE_DIR.
        # Examples:
        #   'logo_path': '/var/www/static/images/knh_logo.png'   # absolute
        #   'logo_path': 'images/knh_logo.png'                   # relative to STATIC_ROOT
        'logo_path': 'images/logo.png',
        'address': {
            'po_box': 'P.O. Box 20723-00202',
            'city': 'Nairobi',
            'country': 'Kenya'
        },
        'contact': {
            'phone': '+254-20-2726300',
            'email': 'calibration@knh.or.ke',
            'website': 'www.knh.or.ke'
        },
        'accreditation': {
            'standard': 'ISO/IEC 17025:2017',
            'body': 'Kenya Accreditation Service (KENAS)',
            'certificate_no': 'CAL-001-2024',
            'valid_until': '2027-12-31'
        }
    }
    
    # Certificate Content Settings
    CONTENT_CONFIG = {
        'include_charts': True,
        'include_uncertainty_budget': True,
        'include_traceability': True,
        'include_environmental_conditions': True,
        'include_qr_code': True,
        'include_watermark': False,
        'watermark_text': 'DRAFT',
        'watermark_opacity': 0.1,
        'max_readings_per_page': 20,
        'decimal_places': 6,
        'scientific_notation_threshold': 0.0001
    }
    
    # Security Features
    SECURITY_CONFIG = {
        'add_security_pattern': True,
        'pattern_opacity': 0.05,
        'pattern_spacing': 20,
        'certificate_validation_url': '/calibration/certificates/validate/{certificate_number}/',
        'qr_code_size': 1 * inch,
        'digital_signature': False  # Set to True when digital signing is implemented
    }
    
    # Uncertainty Calculation Settings
    UNCERTAINTY_CONFIG = {
        'default_coverage_factor': Decimal('2.0'),
        'confidence_level': 95,  # percent
        'type_a_distribution': 'normal',
        'type_b_distribution': 'rectangular',
        'minimum_readings': 3,
        'outlier_detection': True,
        'outlier_method': 'grubbs'  # or 'chauvenet'
    }
    
    # Export Settings
    EXPORT_CONFIG = {
        'pdf_version': '1.4',
        'compression': True,
        'embed_fonts': True,
        'metadata': {
            'title': 'Calibration Certificate',
            'author': 'KNH Calibration Laboratory',
            'subject': 'Equipment Calibration Results',
            'creator': 'CalSoft PDF Generator',
            'producer': 'ReportLab'
        },
        'permissions': {
            'print': True,
            'copy': True,
            'modify': False,
            'annotations': False
        }
    }

class TemplateConfiguration:
    """
    Configuration for different certificate templates.
    """
    
    TEMPLATES = {
        'comprehensive': {
            'name': 'Comprehensive Certificate',
            'description': 'Full certificate with all sections',
            'sections': [
                'header', 'device_info', 'procedure_info', 'environmental',
                'standards', 'results', 'statistics', 'uncertainty',
                'charts', 'traceability', 'signatures', 'footer'
            ],
            'include_charts': True,
            'chart_types': ['uncertainty', 'linearity', 'distribution', 'trend']
        },
        
        'standard': {
            'name': 'Standard Certificate',
            'description': 'Standard certificate with essential information',
            'sections': [
                'header', 'device_info', 'procedure_info', 'environmental',
                'results', 'uncertainty', 'traceability', 'signatures', 'footer'
            ],
            'include_charts': True,
            'chart_types': ['uncertainty', 'linearity']
        },
        
        'summary': {
            'name': 'Summary Certificate',
            'description': 'Compact certificate with key results only',
            'sections': [
                'header', 'device_info', 'results', 'signatures', 'footer'
            ],
            'include_charts': False,
            'chart_types': []
        },
        
        'detailed': {
            'name': 'Detailed Analysis Certificate',
            'description': 'Extended certificate with detailed analysis',
            'sections': [
                'header', 'device_info', 'procedure_info', 'environmental',
                'standards', 'results', 'statistics', 'uncertainty',
                'charts', 'traceability', 'historical_analysis',
                'quality_metrics', 'signatures', 'footer'
            ],
            'include_charts': True,
            'chart_types': ['uncertainty', 'linearity', 'distribution', 'trend', 'control_chart']
        }
    }

class CustomizationOptions:
    """
    Runtime customization options for PDF generation.
    """
    
    @staticmethod
    def get_laboratory_config(lab_code='KNH'):
        """Get laboratory-specific configuration."""
        labs = {
            'KNH': {
                'name': 'KNH CALIBRATION LABORATORY',
                # Relative to STATIC_ROOT or BASE_DIR/static; override with an
                # absolute path if your logo lives elsewhere.
                'logo_path': 'images/knh_logo.png',
                'colors': {
                    'primary': HexColor('#1e40af'),
                    'secondary': HexColor('#0f766e')
                }
            },
            'CUSTOM': {
                'name': 'CUSTOM CALIBRATION LAB',
                'logo_path': 'images/custom_logo.png',
                'colors': {
                    'primary': HexColor('#059669'),
                    'secondary': HexColor('#7c3aed')
                }
            }
        }
        return labs.get(lab_code, labs['KNH'])
    
    @staticmethod
    def get_procedure_specific_config(procedure_type):
        """Get procedure-specific configuration."""
        configs = {
            'electrical': {
                'units': ['V', 'A', 'Ω', 'W', 'Hz'],
                'typical_parameters': ['Voltage', 'Current', 'Resistance', 'Power', 'Frequency'],
                'chart_preferences': ['linearity', 'accuracy_profile']
            },
            'pressure': {
                'units': ['Pa', 'kPa', 'MPa', 'bar', 'psi'],
                'typical_parameters': ['Static Pressure', 'Dynamic Pressure'],
                'chart_preferences': ['linearity', 'hysteresis']
            },
            'temperature': {
                'units': ['°C', 'K', '°F'],
                'typical_parameters': ['Temperature'],
                'chart_preferences': ['linearity', 'stability']
            },
            'flow': {
                'units': ['L/min', 'm³/h', 'cfm'],
                'typical_parameters': ['Flow Rate', 'Volumetric Flow'],
                'chart_preferences': ['linearity', 'repeatability']
            }
        }
        return configs.get(procedure_type, configs.get('electrical'))
    
    @staticmethod
    def apply_user_preferences(base_config, user_preferences):
        """Apply user-specific preferences to base configuration."""
        if not user_preferences:
            return base_config
        
        # Apply color scheme
        if 'color_scheme' in user_preferences:
            scheme = user_preferences['color_scheme']
            if scheme == 'blue':
                base_config.COLORS['primary'] = HexColor('#1e40af')
            elif scheme == 'green':
                base_config.COLORS['primary'] = HexColor('#059669')
            elif scheme == 'purple':
                base_config.COLORS['primary'] = HexColor('#7c3aed')
        
        # Apply chart preferences
        if 'chart_types' in user_preferences:
            base_config.CONTENT_CONFIG['preferred_charts'] = user_preferences['chart_types']
        
        # Apply layout preferences
        if 'layout' in user_preferences:
            layout = user_preferences['layout']
            if layout == 'compact':
                base_config.FONTS['normal']['size'] = 9
                base_config.CHART_CONFIG['height'] = 3 * inch
            elif layout == 'spacious':
                base_config.FONTS['normal']['size'] = 11
                base_config.CHART_CONFIG['height'] = 5 * inch
        
        return base_config

# Default configuration instance
DEFAULT_CONFIG = PDFConfiguration()
TEMPLATE_CONFIG = TemplateConfiguration()
CUSTOMIZATION = CustomizationOptions()

# Configuration validation
def validate_config(config):
    """Validate configuration settings."""
    errors = []
    
    # Validate page size
    if not hasattr(config, 'PAGE_SIZE'):
        errors.append("PAGE_SIZE not defined")
    
    # Validate margins
    if not hasattr(config, 'MARGINS') or not all(k in config.MARGINS for k in ['left', 'right', 'top', 'bottom']):
        errors.append("MARGINS not properly defined")
    
    # Validate colors
    if not hasattr(config, 'COLORS') or not config.COLORS.get('primary'):
        errors.append("Primary color not defined")
    
    # Validate fonts
    if not hasattr(config, 'FONTS') or not config.FONTS.get('normal'):
        errors.append("Normal font not defined")
    
    return errors

# Configuration loader
def load_config(template_name='comprehensive', lab_code='KNH', user_preferences=None):
    """Load configuration with specified template and customizations."""
    try:
        # Start with default configuration
        config = DEFAULT_CONFIG
        
        # Apply laboratory-specific settings
        lab_config = CUSTOMIZATION.get_laboratory_config(lab_code)
        if lab_config:
            config.LABORATORY_INFO.update(lab_config)
        
        # Apply template-specific settings
        template = TEMPLATE_CONFIG.TEMPLATES.get(template_name, TEMPLATE_CONFIG.TEMPLATES['comprehensive'])
        config.CONTENT_CONFIG['template'] = template
        
        # Apply user preferences
        if user_preferences:
            config = CUSTOMIZATION.apply_user_preferences(config, user_preferences)
        
        # Validate configuration
        validation_errors = validate_config(config)
        if validation_errors:
            raise ValueError(f"Configuration validation failed: {validation_errors}")
        
        return config
        
    except Exception as e:
        logger.exception("Error loading PDF configuration")
        return DEFAULT_CONFIG

# Usage examples:
"""
# Basic usage
config = load_config()

# Custom template
config = load_config(template_name='standard')

# Custom laboratory
config = load_config(lab_code='CUSTOM')

# With user preferences
user_prefs = {
    'color_scheme': 'green',
    'layout': 'compact',
    'chart_types': ['uncertainty', 'linearity']
}
config = load_config(user_preferences=user_prefs)

# Full customization
config = load_config(
    template_name='detailed',
    lab_code='KNH',
    user_preferences=user_prefs
)
"""