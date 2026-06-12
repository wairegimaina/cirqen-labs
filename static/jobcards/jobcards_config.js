// Configuration for Job Cards JavaScript
// Add this script before loading jobcards.js

// Set API URLs (these should be set from Django template)
window.LOAD_EQUIPMENT_URL = "{% url 'jobcard:load_equipment' %}";
window.LOAD_ACCESSORIES_URL = "{% url 'jobcard:load_accessories' %}";
window.CHECK_STOCK_URL = "{% url 'jobcard:check_stock_availability' %}";
window.GET_SIGNATURE_URL = "{% url 'jobcard:get_user_signature_data' %}";

// Set workshop ID
window.WORKSHOP_ID = "{{ workshop.id|default:'' }}";

// Set form data for pre-filling
window.FORM_DATA = {
    department: "{{ form_data.department|default:'' }}",
    equipment: "{{ form_data.equipment|default:'' }}"
};

// Set accessories data if available
window.ACCESSORIES_DATA = [
    {% for part in accessories %}
    {
        id: "{{ part.id }}",
        name: "{{ part.name.name|default:'Unknown Part' }}",
        manufacturer: "{{ part.manufacturer.name|default:'Unknown' }}",
        stock_count: {{ part.stock_count|default:0 }}
    }{% if not forloop.last %},{% endif %}
    {% endfor %}
];
