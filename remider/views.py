import sys
import os.path

import requests
from django.conf import settings
from django.http import FileResponse
from django.shortcuts import render, redirect, reverse
from django.utils.translation import ugettext as _
from django.views.generic import TemplateView, FormView

from .api_interactions import change_config_var, create_trigger, notify
from .data_processing import process_nightscouts_api_response, calculate_infusion, calculate_sensor, \
    get_sms_txt_infusion_set, get_sms_txt_sensor, get_trigger_model
from .decorators import secret_key_required, set_language_to_LANGUAGE_CODE
from .forms import ChangeEnvVariableForm, ChooseNotificationsWayForm, GetSecretForm, FileUploudForm, ChooseLanguageForm, \
    TriggerTimeForm
from .storage import OverwriteStorage


@secret_key_required
@set_language_to_LANGUAGE_CODE
def quiet_checkup_view(request):
    """
    shows remaining time to next change (infusion set or CGM sensor)
    without sending notification
    """
    return reminder_and_notifier_view(request, False)


@secret_key_required
@set_language_to_LANGUAGE_CODE
def reminder_and_notifier_view(request, send_notif=True):
    """
    get latest infusion set or CGM sensor change date from Nightscout`s API
    saves it in database
    calculates next change date
    sends notification via sms
    """

    response = requests.get(settings.NIGTSCOUT_LINK + "/api/v1/treatments")
    date, sensor_date = process_nightscouts_api_response(response)

    sms_text = ""

    try:
        infusion_time_remains = calculate_infusion(date)
        inf_text = get_sms_txt_infusion_set(infusion_time_remains)
        sms_text += inf_text

    except TypeError:  # date is None
        inf_text = _(".\n\nInfusion set: unsuccessful data reading")
        sms_text += inf_text

    except Exception as error:
        print(error)
        sys.stdout.flush()
        inf_text = _(".\n\n Infusion set: unsuccessful data processing")
        sms_text += inf_text
    try:
        sensor_time_remains = calculate_sensor(sensor_date)
        sensor_text = get_sms_txt_sensor(sensor_time_remains)
        sms_text += sensor_text

    except TypeError:  # sensor_date is None
        sensor_text = _('\n\nCGM sensor: unsuccessful data reading')
        sms_text += sensor_text

    except Exception as error:
        print(error)
        sys.stdout.flush()
        sensor_text = _("\n\nCGM sensor: unsuccessful data processing")
        sms_text += sensor_text

    if send_notif:
        notify(sms_text)
        create_trigger()

    return render(request, "remider/debug.html",
                  {
                      "inf_text": inf_text[1:],
                      "sensor_text": sensor_text,
                      "SECRET_KEY": settings.SECRET_KEY,
                  })


@set_language_to_LANGUAGE_CODE
def file_view(request):
    """
    view returns verification file for atrigger.com
    """
    file = open(os.path.join(settings.STATIC_ROOT, "uplouded", "ATriggerVerify.txt"), "rb")
    return FileResponse(file)


@set_language_to_LANGUAGE_CODE
def auth_view(request):
    """ authorization view via SECRET_KEY """
    if request.method == "POST":
        form = GetSecretForm(request.POST)
        if form.is_valid():
            return redirect("/menu/?key={}".format(form.cleaned_data['apisecret']))
    else:
        form = GetSecretForm()

    return render(request, "remider/auth.html", {"form": form})


class MenuView(TemplateView):
    """
    menu view
    redirecting buttons and config variables control
    """
    template_name = "remider/menu.html"

    forms_list = []
    forms = (
        ("NIGHTSCOUT_LINK", "ns_link_button", settings.NIGTSCOUT_LINK),
        ("INFUSION_SET_ALERT_FREQUENCY", "infusion_freq_button", settings.INFUSION_SET_ALERT_FREQUENCY),
        ("SENSOR_ALERT_FREQUENCY", "sensor_freq_button", settings.SENSOR_ALERT_FREQUENCY),
        ("ATRIGGER_KEY", "atrigger_key_button", settings.ATRIGGER_KEY),
        ("ATRIGGER_SECRET", "atrigger_secret_button", settings.ATRIGGER_SECRET),
        ("TWILIO_ACCOUNT_SID", "twilio_sid_button", settings.TWILIO_ACCOUNT_SID),
        ("TWILIO_AUTH_TOKEN", "twilio_token_button", settings.TWILIO_AUTH_TOKEN),
    )

    def post(self, request, *args, **kwargs):
        """
        POST method
        handles http`s POST request
        loads forms
        checks if they are submitted
        changes config variables
        """
        self.info = False
        self.info2 = False
        self.forms_list = []
        self.forms_link_dict = {}
        post_data = request.POST or None

        if "language_button" in post_data:
            language_form = ChooseLanguageForm(post_data)
        else:
            language_form = ChooseLanguageForm()
        language_form.fields["language"].initial = settings.LANGUAGE_CODE

        time_model = get_trigger_model()
        if "time_button" in post_data:
            time_form = TriggerTimeForm(post_data, instance=time_model)
        else:
            time_form = TriggerTimeForm(instance=time_model)

        for form_tuple in self.forms:
            form = self.create_changeenvvarform(form_tuple[1], form_tuple[0], form_tuple[2], post_data)
            self.forms_link_dict[form_tuple[0]] = form

        for form_tuple in self.forms:
            form = self.forms_link_dict[form_tuple[0]]
            if form.is_valid() and form_tuple[1] in post_data:
                form, self.info2 = self.save_changeenvvarform(form, form_tuple[0])

        if language_form.is_valid() and "language_button" in post_data:
            language_form, self.info2 = self.save_changeenvvarform(language_form, "LANGUAGE_CODE", "language")
        if time_form.is_valid() and "time_button" in post_data:
            time_form.save()
        contex = self.get_context_data(forms_list=self.forms_list, SECRET_KEY=settings.SECRET_KEY, info=self.info,
                                       info2=self.info2,
                                       language_form=language_form, time_form=time_form, )
        return self.render_to_response(contex)

    def get(self, request, *args, **kwargs):
        """
        GET method
        handles http`s GET request
        loads forms
        shows info about successful change
        """
        language_form = ChooseLanguageForm()
        language_form.fields["language"].initial = settings.LANGUAGE_CODE

        time_model = get_trigger_model()
        time_form = TriggerTimeForm(instance=time_model)

        self.forms_list = []
        self.info = bool(int(request.GET.get("info", "0")))
        self.info2 = False

        for form_tuple in self.forms:
            self.create_changeenvvarform(form_tuple[1], form_tuple[0], form_tuple[2])

        contex = self.get_context_data(forms_list=self.forms_list, SECRET_KEY=settings.SECRET_KEY, info=self.info,
                                       info2=self.info2,
                                       language_form=language_form, time_form=time_form, )
        return self.render_to_response(contex)

    def create_changeenvvarform(self, button_name, label, default, post_data=()):
        """
         creates form and adds it to forms_list
        :param button_name: string, unique button name
        :param label: string, field`s display name
        :param default: string, default value of form`s field
        :param post_data: request.POST or empty tuple
        :return: ready to use form
        """
        if button_name in post_data:
            form = ChangeEnvVariableForm(post_data)
        else:
            form = ChangeEnvVariableForm()
        form.button_name = button_name
        form.fields['new_value'].label = label
        form.fields['new_value'].initial = default
        self.forms_list.append(form)
        return form

    def save_changeenvvarform(self, form, label, field_name="new_value"):
        """
        reads data from submitted form and changes config variables
        :param form: submitted form
        :param label: name of config variable
        :return: already used form and info about successful change
        """
        var = form.cleaned_data[field_name]
        if change_config_var(label, var):
            info2 = (True, label)
        else:
            info2 = (False, "unsuccess")

        return form, info2


@secret_key_required
@set_language_to_LANGUAGE_CODE
def upload_view(request, location=os.path.join(settings.STATIC_ROOT, "uplouded")):
    """ allows user to upload verification file for atrigger.com """
    if request.method == 'POST':
        form = FileUploudForm(request.POST, request.FILES)
        if form.is_valid():
            file = request.FILES['file']
            fs = OverwriteStorage(location=location)
            fs.save("ATriggerVerify.txt", file)
            return redirect("/menu/?key={}&info={}".format(settings.SECRET_KEY, "1"))
    else:
        form = FileUploudForm()
    return render(request, 'remider/upload.html', {'form': form, "SECRET_KEY": settings.SECRET_KEY, })


class ManagePhoneNumbersView(TemplateView):
    """
    allows user to change, add or delete his phone numbers
    """
    template_name = "remider/manage_ph.html"
    forms_list = []
    to_numbers_forms_list = {}
    info = (False, "")

    def post(self, request, *args, **kwargs):
        """
        GET method
        handles http`s GET request
        loads forms
        shows info about successful change
        """
        self.to_numbers_forms_list = {}
        self.forms_list = []
        post_data = request.POST
        from_number_form = self.create_changeenvvarform('from_number_button', _("NUMBER OF SENDER"),
                                                        settings.FROM_NUMBER,
                                                        post_data)

        for i, number in enumerate(settings.TO_NUMBERS):
            label = "to_number_" + str(i + 1)
            button_name = label + "_button"
            label_tag = _("DESTINATION NUMBER ") + str(i + 1) + "."
            form = self.create_changeenvvarform(button_name, label_tag, number, post_data)
            self.to_numbers_forms_list[label] = form
        next_number_id = len(settings.TO_NUMBERS) + 1
        new_number_form = self.create_changeenvvarform('new_number_button',
                                                       _("DESTINATION NUMBER ") + str(next_number_id) + ".", "",
                                                       post_data)

        if from_number_form.is_valid() and 'from_number_button' in post_data:
            from_number_form, self.info = self.save_changeenvvarform(from_number_form, "from_number", )

        for i, number in enumerate(settings.TO_NUMBERS):
            label = "to_number_" + str(i + 1)
            button_name = label + "_button"
            form = self.to_numbers_forms_list[label]
            if form.is_valid() and button_name in post_data:
                form, self.info = self.save_changeenvvarform(form, label)
                break

        if new_number_form.is_valid() and 'new_number_button' in post_data:
            new_number_form, self.info = self.save_changeenvvarform(new_number_form, "to_number_" + str(next_number_id))
        contex = self.get_context_data(forms_list=self.forms_list, info=self.info, delinfo=(False, "normal"),
                                       SECRET_KEY=settings.SECRET_KEY, last_id=self.get_del_id())

        return self.render_to_response(contex)

    def get_del_id(self):
        if bool(int(self.request.GET.get("delinfo", "0"))):
            return str(int(self.request.GET.get("delid", "normal")) - 1)
        else:
            return len(self.to_numbers_forms_list)

    def get(self, request, *args, **kwargs):
        """
        GET method
        handles http`s GET request
        loads forms
        shows info about successful change
        """
        delinfo = (bool(int(request.GET.get("delinfo", "0"))), request.GET.get("delid", "normal"))

        self.forms_list = []
        self.to_numbers_forms_list = {}
        self.create_changeenvvarform('from_number_button', _("NUMBER OF SENDER"), settings.FROM_NUMBER)

        for i, number in enumerate(settings.TO_NUMBERS):
            label = "to_number_" + str(i + 1)
            button_name = label + "_button"
            label_tag = _("DESTINATION NUMBER ") + str(i + 1) + "."
            form = self.create_changeenvvarform(button_name, label_tag, number)
            self.to_numbers_forms_list[label] = form

        next_number_id = len(settings.TO_NUMBERS) + 1
        self.create_changeenvvarform('new_number_button', _("DESTINATION NUMBER ") + str(next_number_id) + ".", "")

        if delinfo[0]:
            id = delinfo[1]
            label = "to_number_" + str(id)
            form = self.to_numbers_forms_list.pop(label)
            self.forms_list.remove(form)
            self.forms_list[-2].deletable = True
            self.forms_list[-1].fields["new_value"].label = _("DESTINATION NUMBER ") + str(
                len(self.to_numbers_forms_list) + 1) + "."
        contex = self.get_context_data(forms_list=self.forms_list, info=self.info, delinfo=delinfo,
                                       SECRET_KEY=settings.SECRET_KEY, last_id=self.get_del_id())

        return self.render_to_response(contex)

    def create_changeenvvarform(self, button_name, label, default, post_data=()):
        """
        creates form and adds it to forms_list
        :param button_name: string, unique button name
        :param label: string, field`s display name
        :param default: string, default value of form`s field
        :param post_data: request.POST or empty tuple
        :return: ready to use form
        """
        if button_name in post_data:
            form = ChangeEnvVariableForm(post_data)
        else:
            form = ChangeEnvVariableForm()

        form.button_name = button_name
        form.fields['new_value'].label = label
        form.fields['new_value'].initial = default
        form.fields["new_value"].required = False
        if form.button_name == 'new_number_button':  # special treatment for adding new number form
            form.action = _("ADD")
            if len(self.forms_list) > 0:
                self.forms_list[-1].deletable = True
        else:
            form.action = _("CHANGE")

        self.forms_list.append(form)
        return form

    def save_changeenvvarform(self, form, label):
        """
        reads data from submitted form and changes config variables
        :param form: submitted form
        :param label: name of config variable
        :return: already used form and info about successful change
        """
        var = form.cleaned_data["new_value"]
        if change_config_var(label, var):
            if form.button_name == 'new_number_button':  # special treatment for adding new number form
                action = _("ADDED")
                if len(self.forms_list) > 1:
                    self.forms_list[-2].deletable = False
                form.action = _("CHANGE")
                form.button_name = label + "_button"
                self.to_numbers_forms_list[label] = form
                next_number_id = len(self.to_numbers_forms_list) + 1
                self.create_changeenvvarform('new_number_button',
                                             _("DESTINATION NUMBER ") + str(next_number_id) + ".",
                                             "")


            else:
                action = _("CHANGED")
            info2 = [True, form.fields['new_value'].label, action]
        else:
            info2 = [False, form.fields['new_value'].label, "unsuccess"]

        return form, info2


@secret_key_required
@set_language_to_LANGUAGE_CODE
def number_delete_view(request, number_id):
    """
     view handles requests for phone number deleting
    :param request: http request
    :param number_id: assigned number of phone number (requested to deleting)
    :return: redirects to phone numbers management view
    """
    label = "to_number_" + str(number_id)

    if change_config_var(label, None):
        deleted = 1
    else:
        deleted = 0

    return redirect("/phonenumbers/?key={}&delinfo={}&delid={}".format(settings.SECRET_KEY, deleted, number_id))


@secret_key_required
@set_language_to_LANGUAGE_CODE
def ifttt_delete_view(request, maker_id):
    """
     view handles requests for IFTTT makers deleting
    :param request: http request
    :param maker_id: assigned number of IFTTT maker (requested to deleting)
    :return: redirects to IFTTT makers management view
    """
    label = "IFTTT_MAKER_" + str(maker_id)
    if change_config_var(label, None):
        deleted = 1
    else:
        deleted = 0
    return redirect("/iftttmakers/?key={}&delinfo={}&delid={}".format(settings.SECRET_KEY, deleted, maker_id))


class NotificationsCenterView(FormView):
    """
    view for notifications management
    """
    form_class = ChooseNotificationsWayForm
    template_name = "remider/notifications.html"

    trig_info = True
    sms_info = True

    def get_initial(self):
        """
        :return: initial values for form
        """
        initial = super(NotificationsCenterView, self).get_initial()
        initial["ifttt_notifications"] = settings.TRIGGER_IFTTT
        initial["sms_notifications"] = settings.SEND_SMS

        return initial

    def get_context_data(self, **kwargs):
        """
        :return: contex data
        """
        return super().get_context_data(**kwargs, SECRET_KEY=settings.SECRET_KEY, trig_info=self.trig_info,
                                        sms_info=self.sms_info)

    def form_valid(self, form):
        """
        method for handling validly submitted forms
        """
        ifttt = form.cleaned_data["ifttt_notifications"]
        sms = form.cleaned_data["sms_notifications"]
        if change_config_var("trigger_ifttt", ifttt):
            self.trig_info = True
        else:
            self.trig_info = False

        if change_config_var("send_sms", sms):
            self.sms_info = True
        else:
            self.sms_info = False

        form.fields["ifttt_notifications"].initial = ifttt
        form.fields["sms_notifications"].initial = sms

        return self.render_to_response(self.get_context_data())


class ManageIFTTTMakersView(TemplateView):
    """
    view for adding, changing and deleting IFTTT makers
    """
    template_name = "remider/manage_ifttt.html"
    forms_list = []
    makers_dict = {}
    info = (False, "")
    ignore_delinfo_in_url = False

    def post(self, request, *args, **kwargs):
        """
        GET method
        handles http`s GET request
        loads forms
        shows info about successful change
        """
        self.makers_dict = {}
        self.forms_list = []
        post_data = request.POST

        for i, maker in enumerate(settings.IFTTT_MAKERS):
            label = "IFTTT_MAKER_" + str(i + 1)
            button_name = label + "_button"
            label_tag = "IFTTT MAKER " + str(i + 1) + "."
            form = self.create_changeenvvarform(button_name, label_tag, maker, post_data)
            self.makers_dict[label] = form
        next_maker_id = len(settings.IFTTT_MAKERS) + 1
        new_maker_form = self.create_changeenvvarform('new_maker_button',
                                                      "IFTTT MAKER " + str(next_maker_id) + ".", "", post_data)

        for i, maker in enumerate(settings.IFTTT_MAKERS):
            label = "IFTTT_MAKER_" + str(i + 1)
            button_name = label + "_button"
            form = self.makers_dict[label]
            if form.is_valid() and button_name in post_data:
                form, self.info = self.save_changeenvvarform(form, label)
                break
        if new_maker_form.is_valid() and 'new_maker_button' in post_data:
            new_maker_form, self.info = self.save_changeenvvarform(new_maker_form, "IFTTT_MAKER_" + str(next_maker_id))
            if self.request.GET.get("delinfo", False) and self.info[0]:
                self.ignore_delinfo_in_url = True
        contex = self.get_context_data(forms_list=self.forms_list, info=self.info, delinfo=(False, "normal"),
                                       SECRET_KEY=settings.SECRET_KEY, last_id=self.get_del_id())

        return self.render_to_response(contex)

    def get(self, request, *args, **kwargs):
        """
        GET method
        handles http`s GET request
        loads forms
        shows info about successful change
        """
        delinfo = (bool(int(request.GET.get("delinfo", "0"))), request.GET.get("delid", "normal"))

        self.forms_list = []
        self.makers_dict = {}

        for i, maker in enumerate(settings.IFTTT_MAKERS):
            label = "IFTTT_MAKER_" + str(i + 1)
            button_name = label + "_button"
            label_tag = "IFTTT MAKER " + str(i + 1) + "."
            form = self.create_changeenvvarform(button_name, label_tag, maker)
            self.makers_dict[label] = form

        next_maker_id = len(settings.IFTTT_MAKERS) + 1
        self.create_changeenvvarform('new_maker_button', "IFTTT MAKER " + str(next_maker_id) + ".", "")

        if delinfo[0] and not self.ignore_delinfo_in_url:
            id = delinfo[1]
            label = "IFTTT_MAKER_" + str(id)
            try:
                form = self.makers_dict.pop(label)
                self.forms_list.remove(form)
                self.forms_list[-2].deletable = True
                self.forms_list[-1].fields["new_value"].label = "IFTTT MAKER " + str(
                    len(self.makers_dict) + 1) + "."
            except KeyError:
                pass
        contex = self.get_context_data(forms_list=self.forms_list, info=self.info, delinfo=delinfo,
                                       SECRET_KEY=settings.SECRET_KEY, last_id=self.get_del_id())

        return self.render_to_response(contex)

    def get_del_id(self):
        if self.ignore_delinfo_in_url:
            return len(settings.IFTTT_MAKERS)

        delinfo = (bool(int(self.request.GET.get("delinfo", "0"))), self.request.GET.get("delid", "normal"))

        return str(int(delinfo[1]) - 1) if delinfo[0] else len(self.makers_dict)

    def create_changeenvvarform(self, button_name, label, default, post_data=()):
        """
        creates form and adds it to forms_list
        :param button_name: string, unique button name
        :param label: string, field`s display name
        :param default: string, default value of form`s field
        :param post_data: request.POST or empty tuple
        :return: ready to use form
        """
        if button_name in post_data:
            form = ChangeEnvVariableForm(post_data)
        else:
            form = ChangeEnvVariableForm()

        form.button_name = button_name
        form.fields['new_value'].label = label
        form.fields['new_value'].initial = default
        form.fields["new_value"].required = False
        if form.button_name == 'new_maker_button':  # special treatment for adding new maker form
            form.action = _("ADD")
            if len(self.forms_list) > 0:
                self.forms_list[-1].deletable = True
        else:
            form.action = _("CHANGE")

        self.forms_list.append(form)
        return form

    def save_changeenvvarform(self, form, label):
        """
        reads data from submitted form and changes config variables
        :param form: submitted form
        :param label: name of config variable
        :return: already used form and info about successful change
        """
        var = form.cleaned_data["new_value"]
        if change_config_var(label, var):
            if form.button_name == 'new_maker_button':  # special treatment for adding new maker form
                action = _("ADDED")
                if len(self.forms_list) > 1:
                    self.forms_list[-2].deletable = False
                form.action = _("CHANGE")
                form.button_name = label + "_button"
                self.makers_dict[label] = form
                next_maker_id = len(self.makers_dict) + 1
                self.create_changeenvvarform('new_maker_button', "IFTTT MAKER " + str(next_maker_id) + ".", "")


            else:
                action = _("CHANGED")
            info2 = [True, form.fields['new_value'].label, action]
        else:
            info2 = [False, form.fields['new_value'].label, "unsuccess"]

        return form, info2
