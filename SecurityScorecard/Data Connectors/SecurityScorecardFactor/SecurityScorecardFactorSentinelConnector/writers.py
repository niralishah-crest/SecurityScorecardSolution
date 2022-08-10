"""This file has class and methods to push factor data into azure sentinel."""
import os
import datetime
import json
import logging
import base64
import hashlib
import hmac
import requests
from .scorecard_exceptions import NoDataError, SSFactorException


SECURITY_SCORECARD_FACTOR_TABLE_NAME = os.environ[
    "SECURITY_SCORECARD_FACTOR_TABLE_NAME"
]
shared_key = os.environ["WorkspaceKey"]
customer_id = os.environ["WorkspaceID"]


class AzureSentinel:
    """AzureSentinel is Used to post data to log analytics."""

    def build_signature(
        self,
        date,
        content_length,
        method,
        content_type,
        resource,
    ):
        """To build the signature."""
        x_headers = "x-ms-date:" + date
        string_to_hash = (
            method
            + "\n"
            + str(content_length)
            + "\n"
            + content_type
            + "\n"
            + x_headers
            + "\n"
            + resource
        )
        bytes_to_hash = bytes(string_to_hash, encoding="utf-8")
        decoded_key = base64.b64decode(shared_key)
        encoded_hash = base64.b64encode(
            hmac.new(decoded_key, bytes_to_hash, digestmod=hashlib.sha256).digest()
        ).decode()
        authorization = "SharedKey {}:{}".format(customer_id, encoded_hash)
        return authorization

    # Build and send a request to the POST API
    def post_data(self, customer_id, body, log_type):
        """Build and send a request to the POST API."""
        method = "POST"
        content_type = "application/json"
        resource = "/api/logs"
        rfc1123date = datetime.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
        content_length = len(body)
        timestamp_date = "ss_time"
        try:
            signature = self.build_signature(
                rfc1123date,
                content_length,
                method,
                content_type,
                resource,
            )
        except SSFactorException as err:
            logging.error("SecurityScorecard Connector: Error occured: {}".format(err))
            raise SSFactorException(
                "SecurityScorecard Connector: Error while generating signature for log analytics."
            )

        uri = (
            "https://"
            + customer_id
            + ".ods.opinsights.azure.com"
            + resource
            + "?api-version=2016-04-01"
        )

        headers = {
            "content-type": content_type,
            "Authorization": signature,
            "Log-Type": log_type,
            "x-ms-date": rfc1123date,
            "time-generated-field": timestamp_date,
        }
        try:
            response = requests.post(uri, data=body, headers=headers)
            if not (response.status_code >= 200 and response.status_code <= 299):
                raise SSFactorException(
                    "SecurityScorecard Connector: Response code: {} from posting data to log analytics.".format(
                        response.status_code
                    )
                )

        except SSFactorException as err:
            logging.error(
                "SecurityScorecard Connector: Error while posting data : {}".format(err)
            )
            raise SSFactorException(
                "SecurityScorecard Connector: Error while posting data to azure sentinel."
            )


class CompanyWriter(object):
    """Represents a companyWriter object."""

    def __init__(self, company, state_manager_object):
        """__init__ method will initialize the object of companywriter class."""
        self.__company = company
        self.azuresentinel = AzureSentinel()
        self.state_manager_object = state_manager_object

    def save_checkpoint(self, portfolio_id=None, **config):
        """save_checkpoint method will save checkpoint for no data available."""
        new_from_date = ""
        try:
            chk_point_name = self.__company.domain
            from_date = config.get("from_date_factor")
            todays_date = str(datetime.datetime.now().date())
            new_from_date = "%s|%s" % (from_date, todays_date)
            check_point_data_all_companies = self.state_manager_object.get()
            check_point_data_all_companies = json.loads(check_point_data_all_companies)
            if portfolio_id is None:
                check_point_data_all_companies[chk_point_name] = new_from_date
            else:
                check_point_data_all_companies[portfolio_id]["companies"][
                    chk_point_name
                ] = new_from_date
            self.state_manager_object.post(json.dumps(check_point_data_all_companies))
            logging.info(
                "SecurityScorecard Connector: checkpoint {} is saved of {} company when data is not available.".format(
                    chk_point_name, new_from_date
                )
            )
        except SSFactorException as err:
            logging.warn("SecurityScorecard Connector: {}".format(err))
            return

    def write_factors(self, portfolio_id=None, **config):
        """write_factors method will process the factors for a company."""
        chk_point_name = ""
        try:
            factors = self.__company.get_factors(**config)
        except NoDataError as err:
            logging.warn("SecurityScorecard Connector: Data : {}".format(err))
            self.save_checkpoint(portfolio_id, **config)
            return
        except SSFactorException as err:
            logging.error("SecurityScorecard Connector: Error occurred {}".format(err))
            raise err
        else:
            new_from_date = ""
            if not factors:
                return
            try:
                chk_point_name = self.__company.domain
                from_date = factors[-1]["dateToday"][:10]
                from_date_data = str(
                    (
                        datetime.datetime.strptime(from_date, "%Y-%m-%d")
                        + datetime.timedelta(days=1)
                    ).date()
                )
                todays_date = str(datetime.datetime.now().date())
                new_from_date = "%s|%s" % (from_date_data, todays_date)
            except SSFactorException as err:
                logging.error("SecurityScorecard Connector: error in checkpoint creation : {}".format(err))
                return
            override = (
                config.get("diff_override_portfolio_factor")
                if config.get("portfolioId") and config.get("portfolioName")
                else config.get("diff_override_own_factor")
            )
            try:
                industry_name = self.__company.get_industry_name()
            except SSFactorException as err:
                logging.error("SecurityScorecard Connector: {}".format(err))

            data_to_post = []
            for factor in factors:
                factor["industry"] = industry_name
                if factor.get("diff") != 0 or override == "true":
                    factor.pop("diff", None)
                    factor["severity"] = config.get("level_factor_change", "NA")
                    factor["portfolioId"] = config.get("portfolioId", "NA")
                    factor["portfolioName"] = config.get("portfolioName", "NA")
                    data_to_post.append(factor)
            data = json.dumps(data_to_post, indent=4)
            self.azuresentinel.post_data(
                customer_id, data, SECURITY_SCORECARD_FACTOR_TABLE_NAME
            )
            check_point_data_all_companies = self.state_manager_object.get()
            check_point_data_all_companies = json.loads(check_point_data_all_companies)
            if portfolio_id is None:
                check_point_data_all_companies[chk_point_name] = new_from_date
            else:
                check_point_data_all_companies[portfolio_id]["companies"][
                    chk_point_name
                ] = new_from_date
            self.state_manager_object.post(json.dumps(check_point_data_all_companies))
            logging.info(
                "SecurityScorecard Connector: checkpoint is saved with {} name and {} date".format(
                    chk_point_name, new_from_date
                )
            )
